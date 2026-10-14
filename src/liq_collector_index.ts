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

const PORT = 3020;
const DB_PATH = new URL("./data/liquidations.db", import.meta.url).pathname;

// ---------- DB ----------
const db = new Database(DB_PATH);
db.exec("PRAGMA journal_mode = WAL;");
db.exec(`
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
`);

const ins = db.prepare(
  `INSERT OR IGNORE INTO liquidations (ts_ms, exchange, symbol, side, pos_side, price, qty, qty_unit, raw, collected_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
);

// ---------- 통계 ----------
const state = {
  started_at: Date.now(),
  binance_ws: "init",
  binance_msgs: 0,
  okx_polls: 0,
  okx_last_ok: null as number | null,
  inserted: 0,
  dedup_skipped: 0,
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
        const ts = Number(o.T || msg.data.E || Date.now());
        // SELL = 롱 포지션 강제청산, BUY = 숏 포지션 강제청산
        countInserted(ins.run(ts, "binance", o.s, o.S, null, Number(o.ap || o.p), Number(o.z || o.l || 0), "coin", JSON.stringify(o), Date.now()));
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
          if (!ts) continue;
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
    by_exchange: rows.map((r) => ({
      exchange: r.exchange,
      n: r.n,
      span_utc: [new Date(r.min_ts).toISOString(), new Date(r.max_ts).toISOString()],
    })),
    last_records: last.map((r) => ({ ...r, iso: new Date(r.ts_ms).toISOString() })),
    recent_errors: state.errors.slice(0, 5),
  };
}

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
