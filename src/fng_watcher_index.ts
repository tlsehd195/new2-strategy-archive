/**
 * F&G 극단 이벤트 감시기 (Task 37-d) — 포트 3021.
 * 목적: F2/X6-R 2027 재검증(PREREG_f2x6_reverify_2027.md)을 위한 신규 극단일 자동 포획.
 *  에이전트 루프 의존 없이 시간당 자기 점검 — 극단(v≤25|v≥75) 발생 시 즉시 장부 기록.
 * 방식: alternative.me API 시간당 폴링 → 일자 dedup → JSONL 장부 append.
 * 상태: GET /stats — 최근값·극단 누적·마지막 점검 시각.
 */
import { readFileSync, appendFileSync, existsSync } from "node:fs";

const PORT = 3021;
const LEDGER = new URL("./data/fng_ledger.jsonl", import.meta.url).pathname;
const THRESH = { fear: 25, greed: 75 }; // F2/X6 셀 thr25/75 양측 정의와 동일 (재검증 규격)

function loadDates(): Set<string> {
  const s = new Set<string>();
  if (existsSync(LEDGER)) {
    for (const line of readFileSync(LEDGER, "utf-8").split("\n")) {
      if (!line.trim()) continue;
      try { s.add(JSON.parse(line).date); } catch {}
    }
  }
  return s;
}
const seen = loadDates();
const state = {
  started_at: Date.now(),
  last_check: null as number | null,
  last_ok: null as number | null,
  checks: 0,
  appended: 0,
  extremes_recorded: 0,
  errors: [] as string[],
};

function noteError(e: unknown) {
  state.errors.unshift(`${new Date().toISOString()} ${String(e).slice(0, 150)}`);
  if (state.errors.length > 20) state.errors.pop();
}

async function check() {
  state.checks++;
  state.last_check = Date.now();
  try {
    const res = await fetch("https://api.alternative.me/fng/?limit=10&format=json", {
      headers: { "User-Agent": "Mozilla/5.0 (research watcher)" },
      signal: AbortSignal.timeout(20_000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const js: any = await res.json();
    for (const row of js.data ?? []) {
      const ts = Number(row.timestamp);
      const d = ts > 10 ** 12
        ? new Date(ts).toISOString().slice(0, 10)
        : new Date(ts * 1000).toISOString().slice(0, 10);
      if (seen.has(d)) continue;
      const v = Number(row.value);
      const extreme = v <= THRESH.fear || v >= THRESH.greed;
      const rec = {
        date: d, value: v, classification: row.value_classification,
        side: v <= THRESH.fear ? "fear" : v >= THRESH.greed ? "greed" : null,
        extreme, fetched_at: new Date().toISOString(),
      };
      appendFileSync(LEDGER, JSON.stringify(rec) + "\n");
      seen.add(d);
      state.appended++;
      if (extreme) state.extremes_recorded++;
      console.log(`[fng] ${d} v=${v}${extreme ? " ★EXTREME" : ""} — ledger appended`);
    }
    state.last_ok = Date.now();
  } catch (e) { noteError(e); }
}

const ledger_rows = () => (existsSync(LEDGER) ? readFileSync(LEDGER, "utf-8").split("\n").filter((l) => l.trim()).length : 0);
const extremes_list = () => {
  if (!existsSync(LEDGER)) return [];
  return readFileSync(LEDGER, "utf-8").split("\n").filter((l) => l.trim())
    .map((l) => { try { return JSON.parse(l); } catch { return null; } })
    .filter((r) => r?.extreme).slice(-10);
};

Bun.serve({
  port: PORT,
  fetch(req) {
    const u = new URL(req.url);
    if (u.pathname === "/stats" || u.pathname === "/api/stats") {
      return new Response(JSON.stringify({
        service: "p3-fng-watcher",
        purpose: "F&G 극단일 자동 포획 — F2/X6-R 2027 재검증 입력 보장 (Task 37-d)",
        thresholds: THRESH,
        uptime_s: Math.floor((Date.now() - state.started_at) / 1000),
        checks: state.checks,
        last_check: state.last_check ? new Date(state.last_check).toISOString() : null,
        last_ok: state.last_ok ? new Date(state.last_ok).toISOString() : null,
        ledger_rows: ledger_rows(),
        appended_this_boot: state.appended,
        extremes_recorded_this_boot: state.extremes_recorded,
        recent_extremes: extremes_list(),
        recent_errors: state.errors.slice(0, 5),
      }, null, 2), { headers: { "Content-Type": "application/json; charset=utf-8" } });
    }
    return new Response(JSON.stringify({ ok: true, hint: "/stats" }), { headers: { "Content-Type": "application/json" } });
  },
});

check().then(() => setInterval(check, 60 * 60 * 1000)); // 부팅 즉시 1회 + 시간당
console.log(`[p3-fng-watcher] started on :${PORT} — ledger ${LEDGER}`);
