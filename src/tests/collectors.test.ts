// ADR 0116 §6/§13: F&G watcher, liquidation collector and instrumentation.
// Isolated temp dirs, injected fetch/clock/sleep: no network, no real data directory.
import { afterEach, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as fng from "../fng_watcher_index";
import * as liq from "../liq_collector_index";
import { checkServiceDir, ensureService, resolveServices } from "../instrumentation";

const dirs: string[] = [];
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "collector-test-"));
  dirs.push(d);
  return d;
}
afterEach(() => {
  while (dirs.length) {
    const d = dirs.pop()!;
    try { chmodSync(d, 0o755); } catch {}
    rmSync(d, { recursive: true, force: true });
  }
});

const DAY = 86_400_000;
const NOW = Date.parse("2026-11-27T06:00:00Z");
const isRoot = typeof process.getuid === "function" && process.getuid() === 0;

function day(iso: string) { return Math.floor(Date.parse(iso + "T00:00:00Z") / 1000); }
function apiRows(dates: string[], value = 50) {
  return { data: dates.map((d) => ({ timestamp: String(day(d)), value: String(value), value_classification: "Neutral" })) };
}
function okResponse(js: unknown) { return new Response(JSON.stringify(js), { status: 200 }); }
const noSleep = async () => {};

describe("data directories (fng, liq, maker share the same rules)", () => {
  test("a missing directory is created, including parents", () => {
    const d = join(tmp(), "a", "b", "c");
    expect(fng.ensureWritableDir(d)).toBe(d);
    expect(existsSync(d)).toBe(true);
    expect(liq.ensureWritableDir(join(d, "liq"))).toBe(join(d, "liq"));
  });

  test("a directory that cannot be created fails loudly", () => {
    const base = tmp();
    writeFileSync(join(base, "file"), "x");
    expect(() => fng.ensureWritableDir(join(base, "file", "sub"))).toThrow(/cannot create data directory/);
    expect(() => liq.openDb(join(base, "file", "sub"))).toThrow(/cannot create data directory/);
  });

  test("a read-only directory fails loudly (permission check)", () => {
    const d = tmp();
    chmodSync(d, 0o555);
    if (isRoot) {
      // root bypasses POSIX permission bits, so access(W_OK) succeeds here; the create-failure
      // test above covers the error path. Recorded in ADR 0116 as an environment limit.
      expect(fng.ensureWritableDir(d)).toBe(d);
    } else {
      expect(() => fng.ensureWritableDir(d)).toThrow(/not writable/);
    }
  });

  test("configured paths: absolute, relative to the service folder, default <service>/data", () => {
    expect(fng.resolveDataDir(undefined, "/srv/fng")).toBe(join("/srv/fng", "data"));
    expect(fng.resolveDataDir("  ", "/srv/fng")).toBe(join("/srv/fng", "data"));
    expect(fng.resolveDataDir("store", "/srv/fng")).toBe(join("/srv/fng", "store"));
    expect(fng.resolveDataDir("/var/fng", "/srv/fng")).toBe("/var/fng");
    expect(liq.resolveDataDir("x", "/srv/liq")).toBe(join("/srv/liq", "x"));
  });

  test("serviceDir uses fileURLToPath (spaces are decoded)", () => {
    expect(fng.serviceDir("file:///tmp/my%20dir/index.ts")).toBe("/tmp/my dir");
  });
});

describe("fng watcher", () => {
  test("API schema errors are rejected, bad rows listed", () => {
    expect(fng.parseResponse({ error: "x" }).schemaError).toMatch(/unexpected response shape/);
    expect(fng.parseResponse(null).schemaError).not.toBeNull();
    const r = fng.parseResponse({ data: [{ timestamp: "abc", value: "5" }, { timestamp: String(day("2026-11-01")), value: "101" },
                                         { timestamp: String(day("2026-11-02")), value: "7.5" },
                                         { timestamp: String(day("2026-11-03")), value: "20" }] });
    expect(r.rows).toEqual([{ date: "2026-11-03", value: 20, classification: null }]);
    expect(r.rejected.length).toBe(3);
  });

  test("duplicates are skipped, conflicting values are not overwritten", () => {
    const st = fng.loadLedger(JSON.stringify({ date: "2026-11-01", value: 30 }) + "\n");
    const up = fng.upsert(st, [{ date: "2026-11-01", value: 30, classification: null },
                               { date: "2026-11-01", value: 30, classification: null },
                               { date: "2026-11-02", value: 40, classification: null },
                               { date: "2026-11-02", value: 41, classification: null }]);
    expect(up.appended.map((r) => r.date)).toEqual(["2026-11-02"]);
    expect(up.duplicates).toBe(2);                          // 11-01 twice in the response + once vs the ledger
    expect(up.conflicts).toEqual([{ date: "2026-11-02", old: 40, new: 41 }]);  // same response, two values
    const st2 = fng.loadLedger(JSON.stringify({ date: "2026-11-01", value: 30 }) + "\n");
    const up2 = fng.upsert(st2, [{ date: "2026-11-01", value: 33, classification: null }]);
    expect(up2.conflicts).toEqual([{ date: "2026-11-01", old: 30, new: 33 }]);
    expect(st2.byDate.get("2026-11-01")).toBe(30);
  });

  test("corrupt ledger lines are reported, not silently dropped", () => {
    const st = fng.loadLedger('{"date":"2026-11-01","value":3}\n{oops\n{"date":"bad","value":1}\n');
    expect(st.corruptLines).toEqual([2, 3]);
    expect(st.byDate.size).toBe(1);
  });

  test("gap detection and request size after a long outage", () => {
    expect(fng.missingDates(["2026-11-01", "2026-11-04"])).toEqual(["2026-11-02", "2026-11-03"]);
    expect(fng.requestLimit(null, NOW, 0)).toBe(fng.MAX_LIMIT);
    expect(fng.requestLimit("2026-11-26", NOW, 0)).toBe(fng.MIN_LIMIT);
    expect(fng.requestLimit("2026-06-01", NOW, 3)).toBe(Math.ceil((NOW - Date.parse("2026-06-01T00:00:00Z")) / DAY) + 2 + 3);
  });

  test("network failure: 3 retries with backoff, then an error is recorded and the ledger is unchanged", async () => {
    const d = tmp();
    const slept: number[] = [];
    let calls = 0;
    const w = fng.createWatcher({ dataDir: d, now: () => NOW, log: () => {},
                                  fetchFn: (async () => { calls++; throw new Error("ECONNRESET"); }) as any,
                                  sleep: async (ms) => { slept.push(ms); } });
    await w.check();
    expect(calls).toBe(4);
    expect(slept).toEqual(fng.RETRY_DELAYS_MS);
    const s = w.stats();
    expect(s.last_ok).toBeNull();
    expect(s.recent_errors[0]).toContain("ECONNRESET");
    expect(existsSync(w.paths.LEDGER)).toBe(false);
    expect(readFileSync(w.paths.RECOVERY, "utf-8")).toContain("ECONNRESET");
  });

  test("HTTP 429 honours Retry-After; 4xx is not retried", async () => {
    const slept: number[] = [];
    let n = 0;
    const js = await fng.fetchWithRetry("u", (async () => (++n === 1
      ? new Response("", { status: 429, headers: { "retry-after": "7" } }) : okResponse({ data: [] }))) as any,
      async (ms) => { slept.push(ms); });
    expect(js).toEqual({ data: [] });
    expect(slept).toEqual([7000]);
    let m = 0;
    await expect(fng.fetchWithRetry("u", (async () => { m++; return new Response("", { status: 404 }); }) as any, noSleep))
      .rejects.toThrow("HTTP 404");
    expect(m).toBe(1);
  });

  test("API error response (bad schema) is an error, never an empty success", async () => {
    const w = fng.createWatcher({ dataDir: tmp(), now: () => NOW, log: () => {}, sleep: noSleep,
                                  fetchFn: (async () => okResponse({ metadata: { error: "rate" } })) as any });
    await w.check();
    expect(w.stats().last_ok).toBeNull();
    expect(w.stats().recent_errors[0]).toContain("unexpected response shape");
  });

  test("recovery after a long outage: missed days are back-filled once, marked recovered, restart is idempotent", async () => {
    const d = tmp();
    writeFileSync(join(d, "fng_ledger.jsonl"),
                  ["2026-11-01", "2026-11-02", "2026-11-04"].map((x) => JSON.stringify({ date: x, value: 50 })).join("\n") + "\n");
    const all: string[] = [];
    for (let t = Date.parse("2026-11-01T00:00:00Z"); t <= Date.parse("2026-11-27T00:00:00Z"); t += DAY) {
      all.push(new Date(t).toISOString().slice(0, 10));
    }
    let lastUrl = "";
    const fetchFn = (async (u: string) => { lastUrl = u; return okResponse(apiRows(all, 10)); }) as any;
    const w = fng.createWatcher({ dataDir: d, now: () => NOW, log: () => {}, sleep: noSleep, fetchFn });
    await w.check();
    const s = w.stats();
    expect(lastUrl).toContain(`limit=${fng.requestLimit("2026-11-04", NOW, 1)}`);
    expect(s.missing_dates_inside_ledger).toEqual([]);
    expect(s.last_date_in_ledger).toBe("2026-11-27");
    expect(s.appended_this_boot).toBe(all.length - 3);
    expect(s.recovered_this_boot).toBeGreaterThan(0);
    expect(s.conflicts_this_boot).toBe(3);                  // value 10 vs stored 50: kept old, logged
    expect(readFileSync(w.paths.CONFLICTS, "utf-8").trim().split("\n").length).toBe(3);
    const ledgerText = readFileSync(w.paths.LEDGER, "utf-8");
    expect(ledgerText).toContain('"recovered":true');
    const w2 = fng.createWatcher({ dataDir: d, now: () => NOW, log: () => {}, sleep: noSleep, fetchFn });
    await w2.check();
    expect(w2.stats().appended_this_boot).toBe(0);
    const ledger = readFileSync(w.paths.LEDGER, "utf-8").trim().split("\n").map((l) => JSON.parse(l).date);
    expect(new Set(ledger).size).toBe(ledger.length);       // no duplicate dates in the ledger
  });

  test("a day the API itself does not return stays listed as missing", async () => {
    const w = fng.createWatcher({ dataDir: tmp(), now: () => NOW, log: () => {}, sleep: noSleep,
                                  fetchFn: (async () => okResponse(apiRows(["2026-11-20", "2026-11-22"]))) as any });
    await w.check();
    expect(w.stats().missing_dates_inside_ledger).toEqual(["2026-11-21"]);
    expect(readFileSync(w.paths.RECOVERY, "utf-8")).toContain("2026-11-21");
  });
});

describe("liquidation collector", () => {
  test("DB opens in a fresh directory and the unique index blocks duplicates", () => {
    const { db, path } = liq.openDb(join(tmp(), "new"));
    expect(existsSync(path)).toBe(true);
    const ins = db.prepare(`INSERT OR IGNORE INTO liquidations (ts_ms, exchange, symbol, side, price, qty, collected_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)`);
    ins.run(1, "binance", "BTCUSDT", "SELL", 100, 1, 2);
    ins.run(1, "binance", "BTCUSDT", "SELL", 100, 1, 3);
    expect((db.query("SELECT COUNT(*) AS n FROM liquidations").get() as any).n).toBe(1);
    db.close();
  });

  test("records without a real timestamp, price or quantity are rejected (no Date.now() fill)", () => {
    expect(liq.validRecord(1_700_000_000_000, 100, 1)).toBeNull();
    expect(liq.validRecord(NaN, 100, 1)).toMatch(/bad ts/);
    expect(liq.validRecord(0, 100, 1)).toMatch(/bad ts/);
    expect(liq.validRecord(1, Number("x"), 1)).toMatch(/bad price/);
    expect(liq.validRecord(1, 100, 0)).toMatch(/bad qty/);
  });
});

describe("instrumentation", () => {
  test("service folders come from config or the working directory, never a fixed machine path", () => {
    const s = resolveServices({}, "/work");
    expect(s.map((x) => x.dir)).toEqual([join("/work", "mini-services", "liq-collector"),
                                         join("/work", "mini-services", "fng-watcher"),
                                         join("/work", "mini-services", "maker-fill-monitor")]);
    const s2 = resolveServices({ NEW2_MINI_SERVICES_DIR: "svc", NEW2_FNG_WATCHER_DIR: "/opt/fng" }, "/work");
    expect(s2[0].dir).toBe(join("/work", "svc", "liq-collector"));
    expect(s2[1].dir).toBe("/opt/fng");
    expect(JSON.stringify(s2)).not.toMatch(new RegExp(["", "home", "z", ""].join("/")));
  });

  test("missing folder or files: not spawned, reason given", async () => {
    const d = tmp();
    expect(checkServiceDir(join(d, "nope"))).toMatch(/not found/);
    mkdirSync(join(d, "svc"));
    writeFileSync(join(d, "svc", "index.ts"), "");
    expect(checkServiceDir(join(d, "svc"))).toMatch(/missing package.json/);
    let spawned = 0;
    const st = await ensureService({ name: "x", port: 1, dir: join(d, "svc") }, () => { spawned++; return {}; },
                                   (m) => import(m), { fetchFn: (async () => { throw new Error("down"); }) as any });
    expect(st).toBe("missing");
    expect(spawned).toBe(0);
  });

  test("alive service is not spawned twice", async () => {
    let spawned = 0;
    const st = await ensureService({ name: "x", port: 1, dir: "/nonexistent" }, () => { spawned++; return {}; },
                                   (m) => import(m), { fetchFn: (async () => new Response("{}")) as any });
    expect(st).toBe("alive");
    expect(spawned).toBe(0);
  });

  test("missing executable: real spawn reports failed without throwing", async () => {
    const d = tmp();
    writeFileSync(join(d, "index.ts"), "");
    writeFileSync(join(d, "package.json"), "{}");
    const { spawn } = await import("node:child_process");
    const st = await ensureService({ name: "x", port: 1, dir: d }, spawn as any, (m) => import(m),
                                   { bunBin: join(d, "no-such-bun"), fetchFn: (async () => { throw new Error("down"); }) as any });
    expect(st).toBe("failed");
    await new Promise((r) => setTimeout(r, 50));            // let the async 'error' event fire (must not crash)
    expect(existsSync(join(d, "service.log"))).toBe(true);
  });
});
