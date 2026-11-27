/**
 * F&G 극단 이벤트 감시기 (Task 37-d) — 포트 3021.
 * 목적: F2/X6-R 2027 재검증(PREREG_f2x6_reverify_2027.md)을 위한 신규 극단일 자동 포획.
 *  에이전트 루프 의존 없이 시간당 자기 점검 — 극단(v≤25|v≥75) 발생 시 즉시 장부 기록.
 * 방식: alternative.me API 시간당 폴링 → 일자 dedup → JSONL 장부 append.
 * 상태: GET /stats — 최근값·극단 누적·마지막 점검 시각·누락 일자·복구 기록.
 *
 * ADR 0116 (지시문 §6):
 *  - 저장 위치는 설정(FNG_DATA_DIR, 기본: 이 파일 옆 data/)이며, 상대경로는 이 파일 위치 기준으로 푼다
 *    (작업 디렉터리와 무관, Windows 경로 포함 — URL.pathname 대신 fileURLToPath).
 *  - 시작 시 저장 폴더를 만들고 쓰기 가능 여부를 검사한다. 실패하면 이유를 출력하고 종료 코드 1로 끝난다
 *    (조용히 실패하지 않는다).
 *  - 복구: 마지막 정상 수집 일자(장부의 최신 일자)를 기준으로 중단 기간을 계산해, 그만큼(+2일,
 *    상한 MAX_LIMIT) 요청한다. 장부 안의 빠진 일자를 찾아 /stats와 recovery 로그에 기록한다.
 *  - upsert: 같은 일자가 이미 있으면 값이 같을 때 건너뛰고, 다르면 덮어쓰지 않고 fng_conflicts.jsonl에
 *    기록한다 (기존 데이터는 절대 덮어쓰지 않음).
 *  - 응답 스키마 검사(data 배열, timestamp 수, value 0..100 정수). 잘못된 행은 버리고 기록한다.
 *  - 재시도: 1회 점검당 최대 3회, 2/4/8초 지수 백오프, HTTP 429는 Retry-After(최대 300초)를 따른다.
 *    점검 주기는 1시간 그대로 — 무제한 재요청 없음.
 */
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { accessSync, constants, mkdirSync } from "node:fs";

export const PORT = 3021;
export const THRESH = { fear: 25, greed: 75 }; // F2/X6 셀 thr25/75 양측 정의와 동일 (재검증 규격)
export const MIN_LIMIT = 10;
export const MAX_LIMIT = 4000; // alternative.me 전체 이력(2018-02 이후)보다 크다
export const RETRY_DELAYS_MS = [2000, 4000, 8000];
const DAY_MS = 86_400_000;

// ---------- 경로 (ADR 0116 §6-1) ----------
export function serviceDir(metaUrl: string): string {
  return dirname(fileURLToPath(metaUrl));
}

/** 설정값(절대 또는 이 파일 기준 상대경로) → 절대경로. 설정이 없으면 <파일 위치>/data. */
export function resolveDataDir(configured: string | undefined, baseDir: string): string {
  if (!configured || !configured.trim()) return join(baseDir, "data");
  return isAbsolute(configured) ? configured : resolve(baseDir, configured);
}

/** 저장 폴더를 만들고 쓰기 권한을 확인한다. 실패하면 이유가 담긴 Error를 던진다. */
export function ensureWritableDir(dir: string): string {
  try {
    mkdirSync(dir, { recursive: true });
  } catch (e) {
    throw new Error(`cannot create data directory ${dir}: ${String(e)}`);
  }
  try {
    accessSync(dir, constants.W_OK);
  } catch (e) {
    throw new Error(`data directory ${dir} is not writable: ${String(e)}`);
  }
  return dir;
}

// ---------- 응답 검사 ----------
export interface FngRow { date: string; value: number; classification: string | null; }

export function isoDay(ts: number): string {
  return new Date(ts > 10 ** 12 ? ts : ts * 1000).toISOString().slice(0, 10);
}

/** API 응답을 검사한다. 잘못된 행은 rejected로 돌려준다(버리되 기록). */
export function parseResponse(js: any): { rows: FngRow[]; rejected: string[]; schemaError: string | null } {
  if (!js || typeof js !== "object" || !Array.isArray(js.data)) {
    return { rows: [], rejected: [], schemaError: `unexpected response shape: ${JSON.stringify(js).slice(0, 120)}` };
  }
  const rows: FngRow[] = [];
  const rejected: string[] = [];
  for (const row of js.data) {
    const ts = Number(row?.timestamp);
    const v = Number(row?.value);
    if (!Number.isFinite(ts) || ts <= 0) { rejected.push(`bad timestamp ${JSON.stringify(row).slice(0, 80)}`); continue; }
    if (!Number.isInteger(v) || v < 0 || v > 100) { rejected.push(`bad value ${JSON.stringify(row).slice(0, 80)}`); continue; }
    rows.push({ date: isoDay(ts), value: v, classification: row.value_classification ?? null });
  }
  return { rows, rejected, schemaError: null };
}

// ---------- 장부 ----------
export interface LedgerState { byDate: Map<string, number>; corruptLines: number[]; }

export function loadLedger(text: string): LedgerState {
  const byDate = new Map<string, number>();
  const corruptLines: number[] = [];
  text.split("\n").forEach((line, i) => {
    if (!line.trim()) return;
    try {
      const r = JSON.parse(line);
      if (typeof r.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(r.date) || !Number.isFinite(Number(r.value))) {
        corruptLines.push(i + 1);
        return;
      }
      if (!byDate.has(r.date)) byDate.set(r.date, Number(r.value));
    } catch {
      corruptLines.push(i + 1);
    }
  });
  return { byDate, corruptLines };
}

/** 장부의 첫 일자~마지막 일자 사이에 빠진 일자. */
export function missingDates(dates: Iterable<string>): string[] {
  const s = [...dates].sort();
  if (s.length < 2) return [];
  const have = new Set(s);
  const out: string[] = [];
  for (let t = Date.parse(s[0] + "T00:00:00Z"); t <= Date.parse(s[s.length - 1] + "T00:00:00Z"); t += DAY_MS) {
    const d = new Date(t).toISOString().slice(0, 10);
    if (!have.has(d)) out.push(d);
  }
  return out;
}

/** 마지막 정상 일자부터 오늘까지 비어 있는 날짜 수 + 여유 2일 → 요청 개수 (상하한 적용). */
export function requestLimit(lastDate: string | null, nowMs: number, missingInside: number): number {
  if (!lastDate) return MAX_LIMIT;
  const gap = Math.ceil((nowMs - Date.parse(lastDate + "T00:00:00Z")) / DAY_MS);
  const want = Math.max(gap, 0) + 2 + missingInside;
  return Math.min(MAX_LIMIT, Math.max(MIN_LIMIT, want));
}

export interface UpsertResult { appended: FngRow[]; duplicates: number; conflicts: { date: string; old: number; new: number }[]; }

/** 새 행만 추가한다. 같은 일자·같은 값은 건너뛰고, 다른 값은 충돌로 돌려준다(덮어쓰지 않음). */
export function upsert(state: LedgerState, rows: FngRow[]): UpsertResult {
  const out: UpsertResult = { appended: [], duplicates: 0, conflicts: [] };
  const seen = new Map<string, number>();
  for (const r of [...rows].sort((a, b) => a.date.localeCompare(b.date))) {
    const first = seen.get(r.date);
    if (first !== undefined) {
      // 같은 응답 안에서 같은 일자가 두 번: 값이 같으면 중복, 다르면 충돌(조용히 버리지 않음)
      if (first === r.value) out.duplicates++;
      else out.conflicts.push({ date: r.date, old: first, new: r.value });
      continue;
    }
    seen.set(r.date, r.value);
    const old = state.byDate.get(r.date);
    if (old === undefined) { out.appended.push(r); state.byDate.set(r.date, r.value); }
    else if (old === r.value) out.duplicates++;
    else out.conflicts.push({ date: r.date, old, new: r.value });
  }
  return out;
}

export function ledgerRecord(r: FngRow, fetchedAt: string, recovered: boolean) {
  const extreme = r.value <= THRESH.fear || r.value >= THRESH.greed;
  return {
    date: r.date, value: r.value, classification: r.classification,
    side: r.value <= THRESH.fear ? "fear" : r.value >= THRESH.greed ? "greed" : null,
    extreme, fetched_at: fetchedAt, ...(recovered ? { recovered: true } : {}),
  };
}

/** 한 번의 요청: 최대 3회 재시도, 429는 Retry-After 준수. */
export async function fetchWithRetry(url: string, fetchFn: typeof fetch,
                                     sleep: (ms: number) => Promise<void>): Promise<any> {
  let last: unknown = null;
  for (let k = 0; k <= RETRY_DELAYS_MS.length; k++) {
    try {
      const res = await fetchFn(url, {
        headers: { "User-Agent": "research-fng-watcher" },
        signal: AbortSignal.timeout(20_000),
      });
      if (res.ok) return await res.json();
      last = new Error(`HTTP ${res.status}`);
      if (res.status !== 429 && res.status < 500) break; // 4xx(429 제외)는 재시도해도 같다
      if (k < RETRY_DELAYS_MS.length) {
        const ra = Number(res.headers.get("retry-after"));
        await sleep(res.status === 429 && Number.isFinite(ra) && ra > 0 ? Math.min(ra, 300) * 1000 : RETRY_DELAYS_MS[k]);
      }
    } catch (e) {
      last = e;
      if (k < RETRY_DELAYS_MS.length) await sleep(RETRY_DELAYS_MS[k]);
    }
  }
  throw last instanceof Error ? last : new Error(String(last));
}

// ---------- 서비스 ----------
export function createWatcher(opts: { dataDir: string; fetchFn?: typeof fetch; now?: () => number;
                                      sleep?: (ms: number) => Promise<void>; log?: (m: string) => void }) {
  const dir = ensureWritableDir(opts.dataDir);
  const LEDGER = join(dir, "fng_ledger.jsonl");
  const CONFLICTS = join(dir, "fng_conflicts.jsonl");
  const RECOVERY = join(dir, "fng_recovery.jsonl");
  const fetchFn = opts.fetchFn ?? fetch;
  const now = opts.now ?? Date.now;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const log = opts.log ?? ((m: string) => console.log(m));
  const ledger = loadLedger(existsSync(LEDGER) ? readFileSync(LEDGER, "utf-8") : "");
  const state = {
    started_at: now(), last_check: null as number | null, last_ok: null as number | null,
    checks: 0, appended: 0, recovered: 0, extremes_recorded: 0, conflicts: 0, rejected_rows: 0,
    errors: [] as string[], corrupt_ledger_lines: ledger.corruptLines,
  };
  const noteError = (e: unknown) => {
    state.errors.unshift(`${new Date(now()).toISOString()} ${String(e).slice(0, 150)}`);
    if (state.errors.length > 20) state.errors.pop();
  };

  async function check(): Promise<void> {
    state.checks++;
    state.last_check = now();
    const dates = [...ledger.byDate.keys()].sort();
    const last = dates.length ? dates[dates.length - 1] : null;
    const inside = missingDates(dates);
    const limit = requestLimit(last, now(), inside.length);
    try {
      const js = await fetchWithRetry(`https://api.alternative.me/fng/?limit=${limit}&format=json`, fetchFn, sleep);
      const { rows, rejected, schemaError } = parseResponse(js);
      if (schemaError) throw new Error(schemaError);
      state.rejected_rows += rejected.length;
      const up = upsert(ledger, rows);
      const fetchedAt = new Date(now()).toISOString();
      for (const r of up.appended) {
        const rec = ledgerRecord(r, fetchedAt, last !== null && r.date < new Date(now() - DAY_MS).toISOString().slice(0, 10));
        appendFileSync(LEDGER, JSON.stringify(rec) + "\n");
        state.appended++;
        if (rec.recovered) state.recovered++;
        if (rec.extreme) state.extremes_recorded++;
        log(`[fng] ${r.date} v=${r.value}${rec.extreme ? " ★EXTREME" : ""}${rec.recovered ? " (recovered)" : ""}`);
      }
      for (const c of up.conflicts) {
        appendFileSync(CONFLICTS, JSON.stringify({ ...c, seen_at: fetchedAt, kept: "old" }) + "\n");
        state.conflicts++;
      }
      const stillMissing = missingDates(ledger.byDate.keys());
      if (inside.length || up.appended.some((r) => r.date < (last ?? "")) || stillMissing.length || rejected.length) {
        appendFileSync(RECOVERY, JSON.stringify({
          at: fetchedAt, requested_limit: limit, last_before: last, missing_before: inside.length,
          appended: up.appended.length, still_missing: stillMissing, rejected,
        }) + "\n");
      }
      state.last_ok = now();
    } catch (e) {
      noteError(e);
      try {
        appendFileSync(RECOVERY, JSON.stringify({ at: new Date(now()).toISOString(), requested_limit: limit,
                                                  last_before: last, error: String(e).slice(0, 200) }) + "\n");
      } catch (w) { noteError(`recovery log write failed: ${String(w)}`); }
    }
  }

  function stats() {
    const dates = [...ledger.byDate.keys()].sort();
    return {
      service: "p3-fng-watcher",
      purpose: "F&G 극단일 자동 포획 — F2/X6-R 2027 재검증 입력 보장 (Task 37-d)",
      thresholds: THRESH, ledger: LEDGER,
      uptime_s: Math.floor((now() - state.started_at) / 1000),
      checks: state.checks,
      last_check: state.last_check ? new Date(state.last_check).toISOString() : null,
      last_ok: state.last_ok ? new Date(state.last_ok).toISOString() : null,
      last_date_in_ledger: dates.length ? dates[dates.length - 1] : null,
      ledger_rows: ledger.byDate.size,
      missing_dates_inside_ledger: missingDates(dates),
      corrupt_ledger_lines: state.corrupt_ledger_lines,
      appended_this_boot: state.appended, recovered_this_boot: state.recovered,
      extremes_recorded_this_boot: state.extremes_recorded, conflicts_this_boot: state.conflicts,
      rejected_rows_this_boot: state.rejected_rows,
      recent_errors: state.errors.slice(0, 5),
    };
  }
  return { check, stats, paths: { LEDGER, CONFLICTS, RECOVERY } };
}

if (import.meta.main) {
  let w: ReturnType<typeof createWatcher>;
  try {
    w = createWatcher({ dataDir: resolveDataDir(process.env.FNG_DATA_DIR, serviceDir(import.meta.url)) });
  } catch (e) {
    console.error(`[p3-fng-watcher] cannot start: ${String(e)}`);
    process.exit(1);
  }
  Bun.serve({
    port: PORT,
    fetch(req) {
      const u = new URL(req.url);
      if (u.pathname === "/stats" || u.pathname === "/api/stats") {
        return new Response(JSON.stringify(w.stats(), null, 2), { headers: { "Content-Type": "application/json; charset=utf-8" } });
      }
      return new Response(JSON.stringify({ ok: true, hint: "/stats" }), { headers: { "Content-Type": "application/json" } });
    },
  });
  w.check().then(() => setInterval(() => void w.check(), 60 * 60 * 1000)); // 부팅 즉시 1회 + 시간당
  console.log(`[p3-fng-watcher] started on :${PORT} — ledger ${w.paths.LEDGER}`);
}
