/**
 * P3 청산 실시간 수집기 (Task 29 착수 인프라)
 * 목적: 청산 역사 소스 전부 불가(OKX=최근만, BitMEX=폐기, Binance allForceOrders=404, Coinglass=키 필요)
 *       → 유일 경로인 '실시간 90일 누적'을 시작. 90일 후(2027-01-11경) P3 측정 가능.
 * 수집: Binance futures forceOrder WebSocket 스트림 (BTCUSDT/ETHUSDT)
 *       + OKX liquidation-orders REST 폴링 (60초 간격 — 최근 청산만 제공되므로 폴링 누적)
 * 저장: SQLite(bun:sqlite) — 프로세스 재시작에도 데이터 유지, UNIQUE 제약으로 중복 차단.
 * 상태: GET /stats — 수집 현황(건수/최종타임스탬프/연결상태) → /api/stats?XTransformPort=3020 으로 접근.
 * 게이트웨이 규격 준수: 이 서비스는 외부(Binance/OKX)로 나가는 클라이언트일 뿐, 외부 요청 URL에 절대경로 사용 안 함.
 */
import { Database } from "bun:sqlite";
import { mkdirSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const PORT = 3020;

// ---------- 저장 위치 (ADR 0116 §6-1) ----------
// LIQ_DATA_DIR(절대 또는 이 파일 기준 상대경로), 없으면 <이 파일 위치>/data.
// URL.pathname은 Windows("/C:/...")와 공백(%20)에서 틀린 경로를 만든다 → fileURLToPath.
export function resolveDataDir(configured: string | undefined, baseDir: string): string {
  if (!configured || !configured.trim()) return join(baseDir, "data");
  return isAbsolute(configured) ? configured : resolve(baseDir, configured);
}

/** 폴더를 만들고 쓰기 권한을 확인한다. 실패는 이유가 담긴 Error (조용히 실패하지 않음). */
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

export const SCHEMA = `
CREATE TABLE IF NOT EXISTS liquidations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL,
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  pos_side TEXT,
  price REAL,
  qty REAL,
  qty_unit TEXT,
  raw TEXT,
  collected_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_liq ON liquidations(exchange, symbol, ts_ms, side, price, qty);
CREATE INDEX IF NOT EXISTS ix_ts ON liquidations(ts_ms);
`;

/** 저장 폴더 생성·쓰기 확인 후 DB를 연다. 실패하면 이유가 담긴 Error. */
export function openDb(dataDir: string): { db: Database; path: string } {
  const dir = ensureWritableDir(dataDir);
  const path = join(dir, "liquidations.db");
  let db: Database;
  try {
    db = new Database(path, { create: true });
    db.exec("PRAGMA journal_mode = WAL;");
    db.exec(SCHEMA);
  } catch (e) {
    throw new Error(`cannot open liquidation DB ${path}: ${String(e)}`);
  }
  return { db, path };
}

/** 한 건의 청산 기록이 저장할 만한지 검사 (시간·가격·수량이 유한한 양수). */
export function validRecord(ts: number, price: number, qty: number): string | null {
  if (!Number.isFinite(ts) || ts <= 0) return `bad ts ${ts}`;
  if (!Number.isFinite(price) || price <= 0) return `bad price ${price}`;
  if (!Number.isFinite(qty) || qty <= 0) return `bad qty ${qty}`;
  return null;
}

let db: Database;
let DB_PATH = "";
let ins: ReturnType<Database["prepare"]>;

// ---------- 통계 ----------
const state = {
  started_at: Date.now(),
  binance_ws: "init",
  binance_msgs: 0,
  okx_polls: 0,
  okx_last_ok: null as number | null,
  inserted: 0,
  dedup_skipped: 0,
  rejected: 0,
  errors: [] as string[],
};
function noteError(src: string, e: unknown) {
  const msg = `${src}: ${String(e).slice(0, 200)}`;
  state.errors.unshift(`${new Date().toISOString()} ${msg}`);
  if (state.errors.length > 20) state.errors.pop();
}

function countInserted(info: { changes: number }) {
  if (info.changes > 0) state.inserted += info.changes;
  else state.dedup_skipped += 1;
}

// ---------- Binance forceOrder WebSocket ----------
function connectBinance() {
  const url = "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder";
  try {
    const ws = new WebSocket(url);
    ws.onopen = () => { state.binance_ws = "open"; console.log("[binance] ws open"); };
    ws.onmessage = (ev) => {
      state.binance_msgs++;
      try {
        const msg = JSON.parse(String(ev.data));
        const o = msg?.data?.o;
        if (!o) return;
        // 이벤트 시각이 없으면 수신 시각으로 대체하지 않는다 (ADR 0116: 대체값 금지)
        const ts = Number(o.T || msg.data.E);
        const price = Number(o.ap || o.p), qty = Number(o.z || o.l);
        const bad = validRecord(ts, price, qty);
        if (bad) { state.rejected++; noteError("binance_invalid", bad); return; }
        // SELL = 롱 포지션 강제청산, BUY = 숏 포지션 강제청산
        countInserted(ins.run(ts, "binance", o.s, o.S, null, price, qty, "coin", JSON.stringify(o), Date.now()));
      } catch (e) { noteError("binance_parse", e); }
    };
    ws.onclose = () => {
      state.binance_ws = "closed";
      console.log("[binance] ws closed — reconnect in 5s");
      setTimeout(connectBinance, 5000);
    };
    ws.onerror = (e) => { noteError("binance_ws", e); try { ws.close(); } catch {} };
  } catch (e) {
    state.binance_ws = "error";
    noteError("binance_connect", e);
    setTimeout(connectBinance, 10000);
  }
}

// ---------- OKX liquidation-orders 폴링 ----------
const OKX_SYMS = ["BTC-USDT", "ETH-USDT"];
async function pollOkx() {
  for (const uly of OKX_SYMS) {
    try {
      const res = await fetch(
        `https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly=${uly}&state=filled&limit=100`,
        { headers: { "User-Agent": "Mozilla/5.0 (research collector)" } }
      );
      if (!res.ok) { noteError("okx_http", `${uly} HTTP ${res.status}`); continue; }
      const js: any = await res.json();
      if (js.code !== "0") { noteError("okx_code", `${uly} ${js.code} ${js.msg}`); continue; }
      state.okx_last_ok = Date.now();
      for (const g of js.data ?? []) {
        const sym = g.uly ?? g.instId ?? uly;
        for (const d of g.details ?? []) {
          const ts = Number(d.time || d.ts || 0);
          const bad = validRecord(ts, Number(d.bkPx), Number(d.sz));
          if (bad) { state.rejected++; noteError("okx_invalid", bad); continue; }
          // posSide long→청산 주문 side sell / short→buy
          countInserted(ins.run(ts, "okx", sym, d.side, d.pos_side, Number(d.bkPx), Number(d.sz), "contract", JSON.stringify(d), Date.now()));
        }
      }
    } catch (e) { noteError("okx_poll", e); }
  }
  state.okx_polls++;
}

// ---------- HTTP 상태 엔드포인트 ----------
function statsPayload() {
  const rows = db
    .query<{ exchange: string; n: number; min_ts: number; max_ts: number }, []>(
      "SELECT exchange, COUNT(*) n, MIN(ts_ms) min_ts, MAX(ts_ms) max_ts FROM liquidations GROUP BY exchange"
    )
    .all();
  const last = db
    .query<{ ts_ms: number; exchange: string; symbol: string; side: string; qty: number }, []>(
      "SELECT ts_ms, exchange, symbol, side, qty FROM liquidations ORDER BY ts_ms DESC LIMIT 3"
    )
    .all();
  return {
    service: "p3-liq-collector",
    purpose: "P3 청산 캐스케이드 90일 실시간 누적 (Task 29 착수, 측정 예정 2027-01-11경)",
    uptime_s: Math.floor((Date.now() - state.started_at) / 1000),
    started_at: new Date(state.started_at).toISOString(),
    binance_ws: state.binance_ws,
    binance_msgs: state.binance_msgs,
    okx_polls: state.okx_polls,
    okx_last_ok: state.okx_last_ok ? new Date(state.okx_last_ok).toISOString() : null,
    inserted: state.inserted,
    dedup_skipped: state.dedup_skipped,
    rejected_invalid: state.rejected,
    db_path: DB_PATH,
    by_exchange: rows.map((r) => ({
      exchange: r.exchange,
      n: r.n,
      span_utc: [new Date(r.min_ts).toISOString(), new Date(r.max_ts).toISOString()],
    })),
    last_records: last.map((r) => ({ ...r, iso: new Date(r.ts_ms).toISOString() })),
    recent_errors: state.errors.slice(0, 5),
  };
}

if (import.meta.main) {
  try {
    const o = openDb(resolveDataDir(process.env.LIQ_DATA_DIR, dirname(fileURLToPath(import.meta.url))));
    db = o.db;
    DB_PATH = o.path;
  } catch (e) {
    console.error(`[p3-liq-collector] cannot start: ${String(e)}`);
    process.exit(1);
  }
  ins = db.prepare(
    `INSERT OR IGNORE INTO liquidations (ts_ms, exchange, symbol, side, pos_side, price, qty, qty_unit, raw, collected_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  );
  Bun.serve({
    port: PORT,
    fetch(req) {
      const u = new URL(req.url);
      if (u.pathname === "/stats" || u.pathname === "/api/stats") {
        return new Response(JSON.stringify(statsPayload(), null, 2), {
          headers: { "Content-Type": "application/json; charset=utf-8" },
        });
      }
      return new Response(JSON.stringify({ ok: true, hint: "/stats" }), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  // ---------- 시작 ----------
  connectBinance();
  pollOkx();
  setInterval(pollOkx, 60_000); // OKX 60초 폴링 — 최근 청산 누적
  console.log(`[p3-liq-collector] started on :${PORT} — DB ${DB_PATH}`);
}
