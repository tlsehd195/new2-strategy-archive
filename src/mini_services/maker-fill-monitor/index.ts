/**
 * maker-fill-monitor (포트 3022) — N2 promotion-basis maker 체결 역학 실측 모니터 (Task 41)
 *
 * 목적: F2/X6-R 2027 재검증(PREREG_f2x6_reverify_2027.md)의 1차 arm = maker promo δ0/K16
 *       promotion-basis의 핵심 가정(체결률)을 실시간 시장에서 실측한다. 승격 심사 시
 *       "백테스트 체결률 1.000"의 실전 할인 계수를 제공하는 것이 유일 목적 — 전략 측정 아님,
 *       실 주문 아님(페이퍼), 승격 주장 아님.
 *
 * 규격 준용: PREREG_n2_maker_exec_2026-10-13.md §2 (promotion-basis 규격)
 *  - δ0 = 유선가 join(라이브에서는 중간가 rest가 불가하므로 best bid/ask join — δ_effective 기록)
 *  - δ5 = mid × (1 ∓ 0.0005), 틱 반올림은 post-only 안전 방향(롱 내림/숏 올림)
 *  - 체결 판정 보수적: 트레이드 가격이 P_lim을 엄격히 통과할 때만 체결 (터치=미체결)
 *  - K16 = 체결 윈도우 4h (만료 시 미체결 폐기 — "미체결은 포기" 규격)
 *  - 셀: {BTC,ETH} × {롱,숏} × δ{0,5} = 8셀, 4h 경계 배치 + 부팅 직후 1회 웜업 배치
 *
 * 어드버스 셀렉션(AS): 체결 후 mid가 체결자에게 불리하게 움직인 정도
 *  - 롱: (mid_fill − mid_check)/mid_fill · 숏: (mid_check − mid_fill)/mid_fill (bp, 양수=불리)
 *  - 체크 시점: 체결 +15분/+1시간/+4시간
 *
 * 영속성: data/fill_ledger.jsonl (fng-watcher 선례 — JSONL append·재생산 가능)
 * 운영 규칙: bun --hot 금지(Task 38 소켓 사고 선례) — 본 서비스는 bun index.ts 직행.
 */

const PORT = 3022;
const LEDGER = `${import.meta.dir}/data/fill_ledger.jsonl`;
import { appendFileSync } from "node:fs";

const SYMBOLS = {
  BTCUSDT: { tick: 0.1 },
  ETHUSDT: { tick: 0.01 },
} as const;
const DELTAS_BP = [0, 5] as const;
const K_WINDOW_MS = 4 * 3600 * 1000; // K16 = 16×15m = 4h
const BOUNDARY_HOURS = [0, 4, 8, 12, 16, 20]; // UTC 4h 경계
const AS_CHECKS_MS = [15 * 60 * 1000, 3600 * 1000, 4 * 3600 * 1000]; // +15m/+1h/+4h
const MID_RECORD_MS = 15 * 1000; // mid 히스토리 샘플링
const MID_HISTORY_CAP = 2200; // 심볼당 ~9h

type Side = "long" | "short";
type Status = "open" | "filled" | "expired";

interface Order {
  id: string;
  symbol: keyof typeof SYMBOLS;
  side: Side;
  delta_bp: number;
  p_lim: number;
  mid_at_quote: number;
  spread_bp: number;
  delta_effective_bp: number;
  placed_ms: number;
  expiry_ms: number;
  status: Status;
  fill_ms?: number;
  ttf_s?: number;
  mid_at_fill?: number;
  batch: string;
}

interface AsCheck {
  order_id: string;
  symbol: keyof typeof SYMBOLS;
  side: Side;
  mid_fill: number;
  horizon_ms: number;
  due_ms: number;
}

// ---------- 상태 ----------
const book = new Map<string, { bids: Map<number, number>; asks: Map<number, number> }>();
const midHist = new Map<string, number[]>(); // 심볼 → mid 배열(시간 균일 아님 — 15s 샘플)
const orders = new Map<string, Order>();
const asPending: AsCheck[] = [];
const asResolved: { symbol: string; side: Side; horizon_ms: number; as_bp: number }[] = [];
let seq = 0;

// WS 카운터
let obMsgs = 0, ptMsgs = 0, wsReconnects = 0, lastWsMsgAt: number | null = null;
let bootMs = Date.now();
let batchesPlaced = 0;

function appendLedger(rec: any) {
  try {
    // node:fs appendFileSync — Bun.write append 옵션이 본 bun 빌드에서 무시됨을 실측(2회)하여 교체.
    // 동기 append로 동시 쓰기 인터리브도 원천 차단.
    appendFileSync(LEDGER, JSON.stringify(rec) + "\n");
  } catch (e) {
    console.error("ledger append failed:", e);
  }
}

function bestBid(s: string): number | null {
  const b = book.get(s)?.bids;
  if (!b || b.size === 0) return null;
  let best: number | null = null;
  for (const p of b.keys()) if (best === null || p > best) best = p;
  return best;
}
function bestAsk(s: string): number | null {
  const a = book.get(s)?.asks;
  if (!a || a.size === 0) return null;
  let best: number | null = null;
  for (const p of a.keys()) if (best === null || p < best) best = p;
  return best;
}
function midOf(s: string): number | null {
  const bb = bestBid(s), ba = bestAsk(s);
  if (bb === null || ba === null) return null;
  return (bb + ba) / 2;
}

function roundTick(price: number, tick: number, dir: "down" | "up"): number {
  const v = price / tick;
  return dir === "down" ? Math.floor(v) * tick : Math.ceil(v) * tick;
}

function spreadBp(s: string): number | null {
  const bb = bestBid(s), ba = bestAsk(s);
  if (bb === null || ba === null || midOf(s) === null) return null;
  return ((ba - bb) / ((bb + ba) / 2)) * 10000;
}

// ---------- 주문 배치 ----------
function placeBatch(label: string) {
  for (const sym of Object.keys(SYMBOLS) as (keyof typeof SYMBOLS)[]) {
    const tick = SYMBOLS[sym].tick;
    const mid = midOf(sym), bb = bestBid(sym), ba = bestAsk(sym);
    if (mid === null || bb === null || ba === null) continue;
    for (const side of ["long", "short"] as Side[]) {
      for (const dbp of DELTAS_BP) {
        let pLim: number;
        let deltaEff: number;
        if (side === "long") {
          pLim = dbp === 0 ? bb : roundTick(mid * (1 - dbp / 10000), tick, "down");
          deltaEff = ((mid - pLim) / mid) * 10000;
        } else {
          pLim = dbp === 0 ? ba : roundTick(mid * (1 + dbp / 10000), tick, "up");
          deltaEff = ((pLim - mid) / mid) * 10000;
        }
        const sp = spreadBp(sym) ?? 0;
        const now = Date.now();
        const o: Order = {
          id: `o${bootMs.toString(36)}${(seq++).toString(36)}`,
          symbol: sym, side, delta_bp: dbp, p_lim: pLim,
          mid_at_quote: mid, spread_bp: sp, delta_effective_bp: deltaEff,
          placed_ms: now, expiry_ms: now + K_WINDOW_MS, status: "open", batch: label,
        };
        orders.set(o.id, o);
        appendLedger({ t: new Date(now).toISOString(), ev: "placed", ...o });
      }
    }
  }
  batchesPlaced++;
}

// ---------- 체결 판정 ----------
function onTrade(sym: string, price: number) {
  const now = Date.now();
  for (const o of orders.values()) {
    if (o.symbol !== sym || o.status !== "open") continue;
    const pass = o.side === "long" ? price < o.p_lim : price > o.p_lim; // 엄격 통과(터치 미체결)
    if (!pass) continue;
    o.status = "filled";
    o.fill_ms = now;
    o.ttf_s = (now - o.placed_ms) / 1000;
    o.mid_at_fill = midOf(sym) ?? o.mid_at_quote;
    appendLedger({ t: new Date(now).toISOString(), ev: "filled", id: o.id, symbol: o.symbol, side: o.side, delta_bp: o.delta_bp, p_lim: o.p_lim, ttf_s: o.ttf_s, mid_at_fill: o.mid_at_fill });
    for (const h of AS_CHECKS_MS) {
      asPending.push({ order_id: o.id, symbol: o.symbol, side: o.side, mid_fill: o.mid_at_fill, horizon_ms: h, due_ms: now + h });
    }
  }
}

// ---------- 주기 작업 ----------
function tickHousekeeping() {
  const now = Date.now();
  // 만료
  for (const o of orders.values()) {
    if (o.status === "open" && now >= o.expiry_ms) {
      o.status = "expired";
      appendLedger({ t: new Date(now).toISOString(), ev: "expired", id: o.id, symbol: o.symbol, side: o.side, delta_bp: o.delta_bp, ttf_s: null });
    }
  }
  // AS 해소
  for (let i = asPending.length - 1; i >= 0; i--) {
    const c = asPending[i];
    if (now < c.due_ms) continue;
    const hist = midHist.get(c.symbol);
    const lastMid = hist && hist.length ? hist[hist.length - 1] : null;
    if (lastMid !== null) {
      const adverse = c.side === "long" ? (c.mid_fill - lastMid) / c.mid_fill : (lastMid - c.mid_fill) / c.mid_fill;
      const asBp = adverse * 10000;
      asResolved.push({ symbol: c.symbol, side: c.side, horizon_ms: c.horizon_ms, as_bp: asBp });
      appendLedger({ t: new Date(now).toISOString(), ev: "as", id: c.order_id, symbol: c.symbol, side: c.side, horizon_ms: c.horizon_ms, as_bp: asBp });
    }
    asPending.splice(i, 1);
  }
}

// 부팅 시 장부 재생산(집계 복원 — 오픈 주문은 리셋 표기)
async function replayLedger() {
  try {
    const f = Bun.file(LEDGER);
    if (!(await f.exists())) return;
    const text = await f.text();
    let placed = 0, filled = 0, expired = 0, orphan = 0;
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try {
        const r = JSON.parse(line);
        if (r.ev === "placed") { placed++; }
        else if (r.ev === "filled") { filled++; }
        else if (r.ev === "expired") { expired++; }
      } catch {}
    }
    // 재시작 시점의 open 주문은 원장상 placed/filled/expired 카운트에 반영되지 않은 미종결 — 정직 표기
    orphan = Math.max(0, placed - filled - expired);
    console.log(`[replay] ledger 복원: placed=${placed} filled=${filled} expired=${expired} 미종결(리셋)=${orphan}`);
  } catch (e) {
    console.error("replay failed:", e);
  }
}

// ---------- Bybit WS ----------
function connectBybit() {
  const ws = new WebSocket("wss://stream.bybit.com/v5/public/linear");
  const subs = [
    ...Object.keys(SYMBOLS).map((s) => `orderbook.1.${s}`),
    ...Object.keys(SYMBOLS).map((s) => `publicTrade.${s}`),
  ];
  ws.onopen = () => {
    ws.send(JSON.stringify({ op: "subscribe", args: subs }));
    console.log("[ws] subscribed:", subs.join(","));
  };
  ws.onmessage = (e) => {
    lastWsMsgAt = Date.now();
    let d: any;
    try { d = JSON.parse(e.data as string); } catch { return; }
    if (d.topic?.startsWith("orderbook")) {
      obMsgs++;
      const sym = d.topic.split(".").pop() as string;
      if (!book.has(sym)) book.set(sym, { bids: new Map(), asks: new Map() });
      const b = book.get(sym)!;
      const apply = (arr: any[], side: "bids" | "asks") => {
        for (const [ps, sz] of arr) {
          const p = parseFloat(ps), s = parseFloat(sz);
          if (s === 0) b[side].delete(p); else b[side].set(p, s);
        }
      };
      if (d.type === "snapshot") { b.bids.clear(); b.asks.clear(); }
      apply(d.data?.b ?? [], "bids");
      apply(d.data?.a ?? [], "asks");
    } else if (d.topic?.startsWith("publicTrade")) {
      ptMsgs++;
      const sym = d.topic.split(".").pop() as string;
      for (const t of d.data ?? []) onTrade(sym, parseFloat(t.p));
    } else if (d.op === "pong") {
      // noop
    }
  };
  ws.onclose = () => {
    wsReconnects++;
    console.log("[ws] closed — 5s 후 재접속");
    setTimeout(connectBybit, 5000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
  // Bybit keepalive
  setInterval(() => { try { ws.send(JSON.stringify({ op: "ping" })); } catch {} }, 20000);
}

// ---------- /stats ----------
function cellStats() {
  const map = new Map<string, { placed: number; filled: number; expired: number; ttf: number[]; dEff: number[] }>();
  for (const o of orders.values()) {
    const key = `${o.symbol}|${o.side}|δ${o.delta_bp}`;
    if (!map.has(key)) map.set(key, { placed: 0, filled: 0, expired: 0, ttf: [], dEff: [] });
    const c = map.get(key)!;
    if (o.status === "filled") { c.filled++; c.ttf.push(o.ttf_s!); }
    else if (o.status === "expired") c.expired++;
    if (o.status !== "open") { c.placed++; c.dEff.push(o.delta_effective_bp); }
  }
  const cells: any[] = [];
  for (const [key, c] of map) {
    const [symbol, side, dbp] = key.split("|");
    const done = c.filled + c.expired;
    cells.push({
      symbol, side, delta_bp: dbp,
      placed: done, filled: c.filled, expired: c.expired,
      fill_rate: done ? +(c.filled / done).toFixed(4) : null,
      ttf_median_s: c.ttf.length ? +median(c.ttf).toFixed(1) : null,
      ttf_mean_s: c.ttf.length ? +(c.ttf.reduce((a, b) => a + b, 0) / c.ttf.length).toFixed(1) : null,
      delta_effective_bp_mean: c.dEff.length ? +(c.dEff.reduce((a, b) => a + b, 0) / c.dEff.length).toFixed(2) : null,
    });
  }
  cells.sort((a, b) => (a.symbol + a.side + a.delta_bp).localeCompare(b.symbol + b.side + b.delta_bp));
  return cells;
}

function median(a: number[]): number {
  const s = [...a].sort((x, y) => x - y);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function asStats() {
  const out: any = { checks_resolved: asResolved.length };
  for (const h of AS_CHECKS_MS) {
    const rs = asResolved.filter((r) => r.horizon_ms === h);
    out[`as_mean_bp_+${h / 60000 >= 60 ? h / 3600000 + "h" : h / 60000 + "m"}`] = rs.length
      ? +(rs.reduce((a, b) => a + b.as_bp, 0) / rs.length).toFixed(2) : null;
  }
  return out;
}

async function handleStats(): Promise<Response> {
  const openN = [...orders.values()].filter((o) => o.status === "open").length;
  return Response.json({
    service: "p3-maker-fill-monitor",
    purpose: "N2 promotion-basis maker 체결 역학 실측 (Task 41) — 페이퍼 모니터·실 주문 아님·승격 주장 아님",
    spec: "PREREG_n2_maker_exec_2026-10-13.md §2 준용 (δ0 join-best·δ5·엄격 통과·K16=4h 만료)",
    uptime_s: Math.round((Date.now() - bootMs) / 1000),
    ws: {
      orderbook_msgs: obMsgs, publictrade_msgs: ptMsgs, reconnects: wsReconnects,
      last_msg_at: lastWsMsgAt ? new Date(lastWsMsgAt).toISOString() : null,
      live: lastWsMsgAt !== null && Date.now() - lastWsMsgAt < 30000,
    },
    batches_placed: batchesPlaced,
    orders: { total_tracked: orders.size, open: openN },
    ledger: LEDGER.replace("/home/z/my-project/", ""),
    cells: cellStats(),
    adverse_selection: asStats(),
    mid_history_samples: Object.fromEntries([...midHist.entries()].map(([k, v]) => [k, v.length])),
  });
}

// ---------- 기동 ----------
Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);
    if (url.pathname === "/stats") return handleStats();
    return Response.json({ ok: true, hint: "/stats" });
  },
});

console.log(`[maker-fill-monitor] listening on ${PORT}`);
await replayLedger();
connectBybit();

// mid 히스토리 샘플러 (15s)
setInterval(() => {
  for (const s of Object.keys(SYMBOLS)) {
    const m = midOf(s);
    if (m === null) continue;
    let arr = midHist.get(s);
    if (!arr) { arr = []; midHist.set(s, arr); }
    arr.push(m);
    if (arr.length > MID_HISTORY_CAP) arr.shift();
  }
}, MID_RECORD_MS);

// 하우스키핑 (30s)
setInterval(tickHousekeeping, 30 * 1000);

// 부팅 웜업 배치: 오더북 안정화 20초 후 1회
setTimeout(() => { placeBatch("boot"); }, 20 * 1000);

// 4h 경계 배치 스케줄러 (10s 그룸)
setInterval(() => {
  const d = new Date();
  if (d.getUTCMinutes() === 0 && d.getUTCHours() % 4 === 0 && d.getUTCSeconds() < 10) {
    placeBatch("boundary");
  }
}, 10 * 1000);
