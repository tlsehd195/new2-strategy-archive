/**
 * maker-fill-monitor (포트 3022) — N2 promotion-basis maker 체결 역학 실측 모니터 (Task 41)
 *
 * 목적: F2/X6-R 2027 재검증(PREREG_f2x6_reverify_2027.md)의 1차 arm = maker promo δ0/K16
 *       promotion-basis의 핵심 가정(체결률)을 실시간 시장에서 실측한다. 승격 심사 시
 *       "백테스트 체결률 1.000"의 실전 할인 계수를 제공하는 것이 유일 목적 — 전략 측정 아님,
 *       실 주문 아님(페이퍼), 승격 주장 아님. 거래소 계정·키·주문 API를 쓰지 않는다(공개 WS만).
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
 * 영속성: <MAKER_DATA_DIR 또는 이 파일 옆 data/>/fill_ledger.jsonl (JSONL append·재생산 가능)
 * 운영 규칙: bun --hot 금지(Task 38 소켓 사고 선례) — 본 서비스는 bun index.ts 직행.
 *
 * ADR 0116 (지시문 §7) — 원장 기반 상태 복원과 통계:
 *  - 모든 이벤트에 eid(이벤트 ID)를 붙인다. 재생 시 같은 eid(옛 줄은 id+ev[+horizon])가 두 번 나오면
 *    내용이 같을 때 중복으로 세고 무시, 다르면 충돌로 기록하고 그 주문을 SUSPECT로 둔다.
 *  - 주문 상태: open → partially_filled → filled | expired | canceled. placed 없이 온 이벤트,
 *    종결 뒤의 이벤트, placed보다 이른 체결 시각은 순서 오류로 기록하고 그 주문은 SUSPECT
 *    (통계에서 따로 센다 — 정상처럼 복구하지 않음). 읽을 수 없는 줄은 줄 번호로 보고한다.
 *    현재 체결 모형은 전량/무체결이라 partial 이벤트를 라이브에서는 만들지 않는다(재생은 지원).
 *  - 재시작: 원장의 미완료 주문을 복원한다. 모니터가 꺼져 있던 동안의 체결은 관측되지 않았으므로
 *    그 주문은 canceled(reason=monitor_downtime)로 닫는다(체결도 만료도 아님). 정상 종료(SIGINT/
 *    SIGTERM)는 열린 주문을 canceled(reason=shutdown)로 기록한다. 끝나지 않은 AS 체크는 만기가
 *    지났으면 as_missed, 아니면 다시 예약한다(나중 mid를 대신 쓰지 않음).
 *  - 통계: placed = 기록된 모든 주문(진행 중 포함), fill_rate = filled / (filled + expired)
 *    (4h 창을 끝까지 관측한 주문). open·canceled·SUSPECT는 분모에서 빼고 각각 센다.
 *    셀별·종목별·일자별(UTC, 주문 시각) 집계를 낸다.
 *  - 원장 쓰기 실패는 숨기지 않는다: 기록되지 않은 주문은 추적하지 않고 ledger_write_failures를 센다.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const PORT = 3022;
export const SYMBOLS = {
  BTCUSDT: { tick: 0.1 },
  ETHUSDT: { tick: 0.01 },
} as const;
export const DELTAS_BP = [0, 5] as const;
export const K_WINDOW_MS = 4 * 3600 * 1000; // K16 = 16×15m = 4h
export const AS_CHECKS_MS = [15 * 60 * 1000, 3600 * 1000, 4 * 3600 * 1000]; // +15m/+1h/+4h
const MID_RECORD_MS = 15 * 1000; // mid 히스토리 샘플링
const MID_HISTORY_CAP = 2200; // 심볼당 ~9h

export type Sym = keyof typeof SYMBOLS;
export type Side = "long" | "short";
export type Status = "open" | "partially_filled" | "filled" | "expired" | "canceled";
const TERMINAL: Status[] = ["filled", "expired", "canceled"];

export interface Order {
  id: string;
  symbol: Sym;
  side: Side;
  delta_bp: number;
  p_lim: number;
  mid_at_quote: number;
  spread_bp: number;
  delta_effective_bp: number;
  placed_ms: number;
  expiry_ms: number;
  status: Status;
  filled_frac?: number;
  fill_ms?: number;
  ttf_s?: number;
  mid_at_fill?: number;
  cancel_reason?: string;
  batch: string;
  suspect?: string[];
  recovered?: boolean;
}

export interface AsCheck {
  order_id: string;
  symbol: Sym;
  side: Side;
  mid_fill: number;
  horizon_ms: number;
  due_ms: number;
}

export interface AsResult { order_id: string; symbol: string; side: Side; horizon_ms: number; as_bp: number | null; missed?: string; }

// ---------- 경로 (ADR 0116 §6-1) ----------
export function resolveDataDir(configured: string | undefined, baseDir: string): string {
  if (!configured || !configured.trim()) return join(baseDir, "data");
  return isAbsolute(configured) ? configured : resolve(baseDir, configured);
}

export function ensureWritableDir(dir: string): string {
  try {
    mkdirSync(dir, { recursive: true });
  } catch (e) {
    throw new Error(`cannot create data directory ${dir}: ${String(e)}`);
  }
  // a real write, not access(W_OK): the permission bits alone are not reliable
  // (bun 1.1 returned success on a 0555 directory; Windows ACLs and network mounts differ)
  const probe = join(dir, `.write-probe-${process.pid}-${Date.now()}`);
  try {
    writeFileSync(probe, "");
    unlinkSync(probe);
  } catch (e) {
    throw new Error(`data directory ${dir} is not writable: ${String(e)}`);
  }
  return dir;
}

// ---------- 원장 이벤트 ----------
/** 이벤트 ID. 옛 원장(eid 없음)은 id+ev(+horizon / +fill_seq)로 같은 키를 만든다. */
export function eventKey(r: any): string {
  if (typeof r.eid === "string" && r.eid) return r.eid;
  if (r.ev === "as" || r.ev === "as_missed") return `${r.id}:as:${r.horizon_ms}`;
  if (r.ev === "partial") return `${r.id}:partial:${r.fill_seq ?? r.t}`;
  return `${r.id}:${r.ev}`;
}

function stable(r: any): string {
  const { t, ...rest } = r ?? {};
  return JSON.stringify(rest, Object.keys(rest).sort());
}

const ID_RE = /^o[0-9a-z]+$/;

export function placedProblem(r: any): string | null {
  if (typeof r.id !== "string" || !ID_RE.test(r.id)) return `bad order id ${JSON.stringify(r.id)}`;
  if (!(r.symbol in SYMBOLS)) return `unknown symbol ${r.symbol}`;
  if (r.side !== "long" && r.side !== "short") return `bad side ${r.side}`;
  for (const k of ["p_lim", "mid_at_quote", "placed_ms", "expiry_ms"]) {
    if (!Number.isFinite(Number(r[k])) || Number(r[k]) <= 0) return `bad ${k} ${r[k]}`;
  }
  if (!(Number(r.expiry_ms) > Number(r.placed_ms))) return "expiry not after placement";
  return null;
}

export interface Anomaly { line: number; kind: string; id?: string; detail: string; }

export interface ReplayReport {
  lines: number;
  events: number;
  corrupt_lines: number[];
  duplicates: number;
  conflicting_duplicates: number;
  anomalies: Anomaly[];
  status: "OK" | "DUPLICATES_IGNORED" | "CORRUPT";
  recoverable: string;
}

export interface LedgerState {
  orders: Map<string, Order>;
  asResolved: AsResult[];
  asDone: Set<string>;   // `${order_id}:${horizon_ms}`
  seenKeys: Map<string, string>;
  report: ReplayReport;
}

export function emptyState(): LedgerState {
  return {
    orders: new Map(), asResolved: [], asDone: new Set(), seenKeys: new Map(),
    report: { lines: 0, events: 0, corrupt_lines: [], duplicates: 0, conflicting_duplicates: 0, anomalies: [],
              status: "OK", recoverable: "all" },
  };
}

function suspect(st: LedgerState, id: string | undefined, line: number, kind: string, detail: string) {
  st.report.anomalies.push({ line, kind, id, detail });
  const o = id ? st.orders.get(id) : undefined;
  if (o) (o.suspect ??= []).push(kind);
}

/** 한 이벤트를 상태에 반영한다 (재생과 라이브가 같은 규칙을 쓴다). */
export function applyEvent(st: LedgerState, r: any, line = 0): void {
  const key = eventKey(r);
  const sig = stable(r);
  const prev = st.seenKeys.get(key);
  if (prev !== undefined) {
    if (prev === sig) st.report.duplicates++;
    else {
      st.report.conflicting_duplicates++;
      suspect(st, r.id, line, "CONFLICTING_DUPLICATE", `event ${key} recorded twice with different content`);
    }
    return;
  }
  st.seenKeys.set(key, sig);
  st.report.events++;
  if (r.ev === "placed") {
    const bad = placedProblem(r);
    if (bad) { st.report.anomalies.push({ line, kind: "INVALID_ORDER", id: r.id, detail: bad }); return; }
    const later = st.orders.get(r.id);
    if (later) { suspect(st, r.id, line, "DUPLICATE_ORDER_ID", "order id placed twice"); return; }
    const o: Order = {
      id: r.id, symbol: r.symbol, side: r.side, delta_bp: Number(r.delta_bp), p_lim: Number(r.p_lim),
      mid_at_quote: Number(r.mid_at_quote), spread_bp: Number(r.spread_bp), delta_effective_bp: Number(r.delta_effective_bp),
      placed_ms: Number(r.placed_ms), expiry_ms: Number(r.expiry_ms), status: "open", batch: String(r.batch ?? ""),
    };
    st.orders.set(o.id, o);
    return;
  }
  if (r.ev === "as" || r.ev === "as_missed") {
    const o = st.orders.get(r.id);
    if (!o || o.status !== "filled") {
      suspect(st, r.id, line, "OUT_OF_ORDER", `${r.ev} for an order that is not filled (${o?.status ?? "unknown"})`);
      return;
    }
    st.asDone.add(`${r.id}:${r.horizon_ms}`);
    st.asResolved.push({ order_id: r.id, symbol: o.symbol, side: o.side, horizon_ms: Number(r.horizon_ms),
                         as_bp: r.ev === "as" ? Number(r.as_bp) : null, ...(r.ev === "as_missed" ? { missed: String(r.reason ?? "") } : {}) });
    return;
  }
  if (!["filled", "partial", "expired", "canceled"].includes(r.ev)) {
    st.report.anomalies.push({ line, kind: "UNKNOWN_EVENT", id: r.id, detail: String(r.ev) });
    return;
  }
  const o = st.orders.get(r.id);
  if (!o) { suspect(st, r.id, line, "OUT_OF_ORDER", `${r.ev} before (or without) its placed event`); return; }
  if (TERMINAL.includes(o.status)) {
    suspect(st, r.id, line, "TRANSITION_AFTER_TERMINAL", `${r.ev} after ${o.status}`);
    return;
  }
  const t = Date.parse(r.t);
  if (Number.isFinite(t) && t < o.placed_ms) suspect(st, r.id, line, "EVENT_BEFORE_PLACEMENT", `${r.ev} at ${r.t}`);
  if (r.ev === "partial") {
    const f = Number(r.frac);
    if (!(f > 0 && f < 1)) { suspect(st, r.id, line, "INVALID_PARTIAL", `frac ${r.frac}`); return; }
    o.filled_frac = Math.min(1, (o.filled_frac ?? 0) + f);
    o.status = "partially_filled";
  } else if (r.ev === "filled") {
    o.status = "filled";
    o.filled_frac = 1;
    o.fill_ms = Number.isFinite(Number(r.fill_ms)) ? Number(r.fill_ms) : t;
    o.ttf_s = Number(r.ttf_s);
    o.mid_at_fill = Number(r.mid_at_fill);
  } else if (r.ev === "expired") {
    o.status = "expired";
  } else {
    o.status = "canceled";
    o.cancel_reason = String(r.reason ?? "unspecified");
  }
}

/** 원장 텍스트 전체를 재생한다. */
export function replayLedger(text: string): LedgerState {
  const st = emptyState();
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    if (!line.trim()) return;
    st.report.lines++;
    let r: any;
    try { r = JSON.parse(line); } catch { st.report.corrupt_lines.push(i + 1); return; }
    if (!r || typeof r !== "object" || typeof r.ev !== "string") { st.report.corrupt_lines.push(i + 1); return; }
    applyEvent(st, r, i + 1);
  });
  finalizeReport(st);
  return st;
}

export function finalizeReport(st: LedgerState): void {
  const rp = st.report;
  const bad = rp.corrupt_lines.length + rp.conflicting_duplicates + rp.anomalies.length;
  rp.status = bad ? "CORRUPT" : rp.duplicates ? "DUPLICATES_IGNORED" : "OK";
  const sus = [...st.orders.values()].filter((o) => o.suspect?.length).length;
  rp.recoverable = bad
    ? `orders without anomalies (${st.orders.size - sus} of ${st.orders.size}); ${sus} SUSPECT orders and `
      + `${rp.corrupt_lines.length} unreadable lines are reported, not restored as normal`
    : "all";
}

/** 재시작 직후: 미완료 주문과 끝나지 않은 AS 체크를 정리할 이벤트들(원장에 append할 것). */
export function recoveryEvents(st: LedgerState, nowMs: number): { events: any[]; pending: AsCheck[] } {
  const events: any[] = [];
  const pending: AsCheck[] = [];
  const iso = new Date(nowMs).toISOString();
  for (const o of st.orders.values()) {
    if (o.suspect?.length) continue;
    if (o.status === "open" || o.status === "partially_filled") {
      o.recovered = true;
      events.push({ t: iso, ev: "canceled", eid: `${o.id}:canceled`, id: o.id, symbol: o.symbol, side: o.side,
                    delta_bp: o.delta_bp, reason: "monitor_downtime",
                    detail: "trades while the monitor was down were not observed: neither filled nor expired" });
    } else if (o.status === "filled" && o.fill_ms !== undefined && o.mid_at_fill !== undefined) {
      for (const h of AS_CHECKS_MS) {
        if (st.asDone.has(`${o.id}:${h}`)) continue;
        const due = o.fill_ms + h;
        if (due <= nowMs) {
          events.push({ t: iso, ev: "as_missed", eid: `${o.id}:as:${h}`, id: o.id, symbol: o.symbol, side: o.side,
                        horizon_ms: h, reason: "monitor_downtime" });
        } else {
          pending.push({ order_id: o.id, symbol: o.symbol, side: o.side, mid_fill: o.mid_at_fill, horizon_ms: h, due_ms: due });
        }
      }
    }
  }
  return { events, pending };
}

// ---------- 통계 ----------
export interface Cell {
  placed: number; open: number; partially_filled: number; filled: number; expired: number; canceled: number;
  suspect: number; ttf: number[]; dEff: number[];
}

function emptyCell(): Cell {
  return { placed: 0, open: 0, partially_filled: 0, filled: 0, expired: 0, canceled: 0, suspect: 0, ttf: [], dEff: [] };
}

function addOrder(c: Cell, o: Order) {
  if (o.suspect?.length) { c.suspect++; return; }
  c.placed++;
  c[o.status]++;
  c.dEff.push(o.delta_effective_bp);
  if (o.status === "filled" && Number.isFinite(o.ttf_s)) c.ttf.push(o.ttf_s!);
}

export function median(a: number[]): number {
  const s = [...a].sort((x, y) => x - y);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export const FILL_RATE_DENOMINATOR =
  "filled + expired: orders whose 4h window was observed to the end; open, canceled (shutdown / monitor downtime) "
  + "and SUSPECT (ledger anomaly) orders are counted separately and are not in the denominator";

function render(c: Cell) {
  const resolved = c.filled + c.expired;
  return {
    placed: c.placed, open: c.open, partially_filled: c.partially_filled, filled: c.filled, expired: c.expired,
    canceled: c.canceled, suspect: c.suspect, resolved,
    fill_rate: resolved ? +(c.filled / resolved).toFixed(4) : null,
    ttf_median_s: c.ttf.length ? +median(c.ttf).toFixed(1) : null,
    ttf_mean_s: c.ttf.length ? +(c.ttf.reduce((a, b) => a + b, 0) / c.ttf.length).toFixed(1) : null,
    delta_effective_bp_mean: c.dEff.length ? +(c.dEff.reduce((a, b) => a + b, 0) / c.dEff.length).toFixed(2) : null,
  };
}

/** 셀별·종목별·일자별 체결 통계. 진행 중 주문을 빼지 않는다(open으로 센다). */
export function cellStats(orders: Iterable<Order>) {
  const cells = new Map<string, Cell>();
  const bySym = new Map<string, Cell>();
  const byDay = new Map<string, Cell>();
  const get = (m: Map<string, Cell>, k: string) => { if (!m.has(k)) m.set(k, emptyCell()); return m.get(k)!; };
  for (const o of orders) {
    addOrder(get(cells, `${o.symbol}|${o.side}|δ${o.delta_bp}`), o);
    addOrder(get(bySym, o.symbol), o);
    addOrder(get(byDay, new Date(o.placed_ms).toISOString().slice(0, 10)), o);
  }
  const cellRows = [...cells.entries()].map(([k, c]) => {
    const [symbol, side, delta_bp] = k.split("|");
    return { symbol, side, delta_bp, ...render(c) };
  }).sort((a, b) => (a.symbol + a.side + a.delta_bp).localeCompare(b.symbol + b.side + b.delta_bp));
  return {
    denominator: FILL_RATE_DENOMINATOR,
    cells: cellRows,
    by_symbol: Object.fromEntries([...bySym.entries()].sort().map(([k, c]) => [k, render(c)])),
    by_day_utc: Object.fromEntries([...byDay.entries()].sort().map(([k, c]) => [k, render(c)])),
  };
}

export function asStats(asResolved: AsResult[]) {
  const out: any = { checks_resolved: asResolved.filter((r) => r.as_bp !== null).length,
                     checks_missed: asResolved.filter((r) => r.as_bp === null).length };
  for (const h of AS_CHECKS_MS) {
    const rs = asResolved.filter((r) => r.horizon_ms === h && r.as_bp !== null);
    out[`as_mean_bp_+${h / 60000 >= 60 ? h / 3600000 + "h" : h / 60000 + "m"}`] = rs.length
      ? +(rs.reduce((a, b) => a + (b.as_bp as number), 0) / rs.length).toFixed(2) : null;
  }
  return out;
}

// ---------- 시세 상태 (라이브) ----------
const book = new Map<string, { bids: Map<number, number>; asks: Map<number, number> }>();
const midHist = new Map<string, number[]>(); // 심볼 → mid 배열(시간 균일 아님 — 15s 샘플)

export function roundTick(price: number, tick: number, dir: "down" | "up"): number {
  const v = price / tick;
  return dir === "down" ? Math.floor(v) * tick : Math.ceil(v) * tick;
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
function spreadBp(s: string): number | null {
  const bb = bestBid(s), ba = bestAsk(s);
  if (bb === null || ba === null) return null;
  return ((ba - bb) / ((bb + ba) / 2)) * 10000;
}

// ---------- 모니터 (원장 + 상태) ----------
export function createMonitor(opts: { dataDir: string; now?: () => number; append?: (path: string, line: string) => void }) {
  const dir = ensureWritableDir(opts.dataDir);
  const LEDGER = join(dir, "fill_ledger.jsonl");
  const now = opts.now ?? Date.now;
  const append = opts.append ?? ((p: string, l: string) => appendFileSync(p, l));
  const st = replayLedger(existsSync(LEDGER) ? readFileSync(LEDGER, "utf-8") : "");
  const bootMs = now();
  let seq = 0, writeFailures = 0, batchesPlaced = 0;
  const asPending: AsCheck[] = [];

  /** 원장에 쓰고, 쓰인 경우에만 상태에 반영한다. */
  function record(rec: any): boolean {
    try {
      append(LEDGER, JSON.stringify(rec) + "\n");
    } catch (e) {
      writeFailures++;
      console.error(`ledger append failed: ${String(e)}`);
      return false;
    }
    applyEvent(st, rec);
    return true;
  }

  const rec0 = recoveryEvents(st, now());
  for (const ev of rec0.events) record(ev);
  asPending.push(...rec0.pending);
  finalizeReport(st);
  console.log(`[replay] ledger ${st.report.status}: orders=${st.orders.size} events=${st.report.events} `
              + `duplicates=${st.report.duplicates} anomalies=${st.report.anomalies.length} `
              + `corrupt_lines=${st.report.corrupt_lines.length} recovered_open=${rec0.events.filter((e) => e.ev === "canceled").length}`);

  function placeOrder(sym: Sym, side: Side, dbp: number, label: string, q: { mid: number; bb: number; ba: number; sp: number }) {
    const tick = SYMBOLS[sym].tick;
    let pLim: number, deltaEff: number;
    if (side === "long") {
      pLim = dbp === 0 ? q.bb : roundTick(q.mid * (1 - dbp / 10000), tick, "down");
      deltaEff = ((q.mid - pLim) / q.mid) * 10000;
    } else {
      pLim = dbp === 0 ? q.ba : roundTick(q.mid * (1 + dbp / 10000), tick, "up");
      deltaEff = ((pLim - q.mid) / q.mid) * 10000;
    }
    const t = now();
    const id = `o${bootMs.toString(36)}${(seq++).toString(36)}`;
    return record({ t: new Date(t).toISOString(), ev: "placed", eid: `${id}:placed`, id, symbol: sym, side, delta_bp: dbp,
                    p_lim: pLim, mid_at_quote: q.mid, spread_bp: q.sp, delta_effective_bp: deltaEff,
                    placed_ms: t, expiry_ms: t + K_WINDOW_MS, status: "open", batch: label });
  }

  function placeBatch(label: string, quote: (s: Sym) => { mid: number; bb: number; ba: number; sp: number } | null) {
    for (const sym of Object.keys(SYMBOLS) as Sym[]) {
      const q = quote(sym);
      if (!q) continue;
      for (const side of ["long", "short"] as Side[]) for (const dbp of DELTAS_BP) placeOrder(sym, side, dbp, label, q);
    }
    batchesPlaced++;
  }

  function onTrade(sym: string, price: number, midNow: number | null) {
    const t = now();
    for (const o of st.orders.values()) {
      if (o.symbol !== sym || (o.status !== "open" && o.status !== "partially_filled") || o.suspect?.length) continue;
      if (t >= o.expiry_ms) continue; // 만료 처리 전 — 창 밖 체결은 세지 않음
      const pass = o.side === "long" ? price < o.p_lim : price > o.p_lim; // 엄격 통과(터치 미체결)
      if (!pass) continue;
      const mid = midNow ?? o.mid_at_quote;
      const ok = record({ t: new Date(t).toISOString(), ev: "filled", eid: `${o.id}:filled`, id: o.id, symbol: o.symbol,
                          side: o.side, delta_bp: o.delta_bp, p_lim: o.p_lim, fill_ms: t, ttf_s: (t - o.placed_ms) / 1000,
                          mid_at_fill: mid });
      if (ok) for (const h of AS_CHECKS_MS) {
        asPending.push({ order_id: o.id, symbol: o.symbol, side: o.side, mid_fill: mid, horizon_ms: h, due_ms: t + h });
      }
    }
  }

  function housekeeping(lastMid: (s: string) => number | null) {
    const t = now();
    for (const o of st.orders.values()) {
      if ((o.status === "open" || o.status === "partially_filled") && !o.suspect?.length && t >= o.expiry_ms) {
        record({ t: new Date(t).toISOString(), ev: "expired", eid: `${o.id}:expired`, id: o.id, symbol: o.symbol,
                 side: o.side, delta_bp: o.delta_bp, ttf_s: null });
      }
    }
    for (let i = asPending.length - 1; i >= 0; i--) {
      const c = asPending[i];
      if (t < c.due_ms) continue;
      const m = lastMid(c.symbol);
      if (m !== null) {
        const adverse = c.side === "long" ? (c.mid_fill - m) / c.mid_fill : (m - c.mid_fill) / c.mid_fill;
        record({ t: new Date(t).toISOString(), ev: "as", eid: `${c.order_id}:as:${c.horizon_ms}`, id: c.order_id,
                 symbol: c.symbol, side: c.side, horizon_ms: c.horizon_ms, as_bp: adverse * 10000 });
      } else {
        record({ t: new Date(t).toISOString(), ev: "as_missed", eid: `${c.order_id}:as:${c.horizon_ms}`, id: c.order_id,
                 symbol: c.symbol, side: c.side, horizon_ms: c.horizon_ms, reason: "no_mid_at_due_time" });
      }
      asPending.splice(i, 1);
    }
  }

  function shutdown(reason = "shutdown") {
    const t = now();
    for (const o of st.orders.values()) {
      if ((o.status === "open" || o.status === "partially_filled") && !o.suspect?.length) {
        record({ t: new Date(t).toISOString(), ev: "canceled", eid: `${o.id}:canceled`, id: o.id, symbol: o.symbol,
                 side: o.side, delta_bp: o.delta_bp, reason });
      }
    }
  }

  function stats() {
    finalizeReport(st);
    const openN = [...st.orders.values()].filter((o) => o.status === "open" || o.status === "partially_filled").length;
    return {
      ledger: LEDGER, ledger_integrity: st.report, ledger_write_failures: writeFailures,
      batches_placed_this_boot: batchesPlaced,
      orders: { total_tracked: st.orders.size, open: openN },
      ...cellStats(st.orders.values()),
      adverse_selection: asStats(st.asResolved),
      as_pending: asPending.length,
    };
  }

  return { st, asPending, placeBatch, onTrade, housekeeping, shutdown, stats, LEDGER };
}

// ---------- Bybit WS (공개 시세만) ----------
function connectBybit(mon: ReturnType<typeof createMonitor>, counters: any) {
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
    counters.lastWsMsgAt = Date.now();
    let d: any;
    try { d = JSON.parse(e.data as string); } catch { return; }
    if (d.topic?.startsWith("orderbook")) {
      counters.obMsgs++;
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
      counters.ptMsgs++;
      const sym = d.topic.split(".").pop() as string;
      for (const t of d.data ?? []) mon.onTrade(sym, parseFloat(t.p), midOf(sym));
    }
  };
  const ping = setInterval(() => { try { ws.send(JSON.stringify({ op: "ping" })); } catch {} }, 20000);
  ws.onclose = () => {
    clearInterval(ping);
    counters.wsReconnects++;
    console.log("[ws] closed — 5s 후 재접속");
    setTimeout(() => connectBybit(mon, counters), 5000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
}

if (import.meta.main) {
  let mon: ReturnType<typeof createMonitor>;
  try {
    mon = createMonitor({ dataDir: resolveDataDir(process.env.MAKER_DATA_DIR, dirname(fileURLToPath(import.meta.url))) });
  } catch (e) {
    console.error(`[maker-fill-monitor] cannot start: ${String(e)}`);
    process.exit(1);
  }
  const bootMs = Date.now();
  const counters = { obMsgs: 0, ptMsgs: 0, wsReconnects: 0, lastWsMsgAt: null as number | null };
  const quote = (s: Sym) => {
    const mid = midOf(s), bb = bestBid(s), ba = bestAsk(s);
    return mid === null || bb === null || ba === null ? null : { mid, bb, ba, sp: spreadBp(s) ?? 0 };
  };
  const lastMid = (s: string) => { const h = midHist.get(s); return h && h.length ? h[h.length - 1] : null; };
  Bun.serve({
    port: PORT,
    async fetch(req) {
      const url = new URL(req.url);
      if (url.pathname === "/stats") {
        return Response.json({
          service: "p3-maker-fill-monitor",
          purpose: "N2 promotion-basis maker 체결 역학 실측 (Task 41) — 페이퍼 모니터·실 주문 아님·승격 주장 아님",
          spec: "PREREG_n2_maker_exec_2026-10-13.md §2 준용 (δ0 join-best·δ5·엄격 통과·K16=4h 만료)",
          uptime_s: Math.round((Date.now() - bootMs) / 1000),
          ws: { orderbook_msgs: counters.obMsgs, publictrade_msgs: counters.ptMsgs, reconnects: counters.wsReconnects,
                last_msg_at: counters.lastWsMsgAt ? new Date(counters.lastWsMsgAt).toISOString() : null,
                live: counters.lastWsMsgAt !== null && Date.now() - counters.lastWsMsgAt < 30000 },
          ...mon.stats(),
          mid_history_samples: Object.fromEntries([...midHist.entries()].map(([k, v]) => [k, v.length])),
        });
      }
      return Response.json({ ok: true, hint: "/stats" });
    },
  });
  console.log(`[maker-fill-monitor] listening on ${PORT} — ledger ${mon.LEDGER}`);
  connectBybit(mon, counters);
  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    process.on(sig, () => { mon.shutdown("shutdown"); process.exit(0); });
  }
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
  setInterval(() => mon.housekeeping(lastMid), 30 * 1000); // 하우스키핑 (30s)
  setTimeout(() => mon.placeBatch("boot", quote), 20 * 1000); // 부팅 웜업 배치: 오더북 안정화 20초 후 1회
  // 4h 경계 배치 스케줄러 (10s 그룸) — 같은 경계에서 두 번 배치하지 않는다
  let lastBoundary = "";
  setInterval(() => {
    const d = new Date();
    const key = d.toISOString().slice(0, 13);
    if (d.getUTCMinutes() === 0 && d.getUTCHours() % 4 === 0 && d.getUTCSeconds() < 10 && key !== lastBoundary) {
      lastBoundary = key;
      mon.placeBatch("boundary", quote);
    }
  }, 10 * 1000);
}
