// ADR 0116 §7: maker fill monitor ledger restore. Isolated temp dirs only; no network.
import { afterEach, describe, expect, test } from "bun:test";
import { appendFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  AS_CHECKS_MS, K_WINDOW_MS, cellStats, createMonitor, eventKey, replayLedger, recoveryEvents,
} from "../maker_fill_monitor_index";

const dirs: string[] = [];
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), "maker-test-"));
  dirs.push(d);
  return d;
}
afterEach(() => { while (dirs.length) rmSync(dirs.pop()!, { recursive: true, force: true }); });

const T0 = Date.parse("2026-11-20T00:00:00Z");
const quote = (s: string) => (s === "BTCUSDT" ? { mid: 100_000, bb: 99_999.9, ba: 100_000.1, sp: 0.02 }
                                              : { mid: 3_000, bb: 2_999.99, ba: 3_000.01, sp: 0.07 });

function clock(start = T0) {
  let t = start;
  return { now: () => t, set: (v: number) => { t = v; }, add: (ms: number) => { t += ms; } };
}

function placed(id: string, extra: Record<string, unknown> = {}) {
  return { t: new Date(T0).toISOString(), ev: "placed", eid: `${id}:placed`, id, symbol: "BTCUSDT", side: "long",
           delta_bp: 0, p_lim: 99_999.9, mid_at_quote: 100_000, spread_bp: 0.02, delta_effective_bp: 0.01,
           placed_ms: T0, expiry_ms: T0 + K_WINDOW_MS, status: "open", batch: "b", ...extra };
}
const lines = (...rs: object[]) => rs.map((r) => JSON.stringify(r)).join("\n") + "\n";

describe("restart restores state from the ledger", () => {
  test("program stops and restarts: filled/expired orders and stats are identical", () => {
    const dir = tmp();
    const c = clock();
    const m1 = createMonitor({ dataDir: dir, now: c.now });
    m1.placeBatch("2026-11-20T00", quote);                 // 2 symbols x 2 sides x 2 deltas = 8 orders
    c.add(60_000);
    m1.onTrade("BTCUSDT", 99_000, 99_500);                 // fills BTC long d0 and d5
    c.add(K_WINDOW_MS);
    m1.housekeeping(() => 99_600);                          // the rest expire, AS checks become due
    const before = m1.stats();
    const m2 = createMonitor({ dataDir: dir, now: c.now });
    const after = m2.stats();
    expect(after.cells).toEqual(before.cells);
    expect(after.by_symbol).toEqual(before.by_symbol);
    expect(after.orders.total_tracked).toBe(8);
    expect(after.ledger_integrity.status).toBe("OK");
    expect(after.adverse_selection).toEqual(before.adverse_selection);
  });

  test("orders placed then the process dies: recovered as canceled (monitor_downtime), not filled or expired", () => {
    const dir = tmp();
    const c = clock();
    createMonitor({ dataDir: dir, now: c.now }).placeBatch("b", quote); // no shutdown() call: a crash
    c.add(10 * 3600_000);
    const m2 = createMonitor({ dataDir: dir, now: c.now });
    const s = m2.stats();
    expect(s.orders.open).toBe(0);
    const btc = s.by_symbol.BTCUSDT;
    expect(btc.canceled).toBe(4);
    expect(btc.filled + btc.expired).toBe(0);
    expect(btc.fill_rate).toBeNull();                       // nothing observed to the end -> no rate
    const text = readFileSync(m2.LEDGER, "utf-8");
    expect(text).toContain("monitor_downtime");
    // a third start does not cancel again (event ids dedupe)
    const m3 = createMonitor({ dataDir: dir, now: c.now });
    expect(m3.st.report.duplicates).toBe(0);
    expect(readFileSync(m3.LEDGER, "utf-8")).toBe(text);
  });

  test("partial fill then shutdown: restart keeps the partial state and closes it as canceled", () => {
    const st = replayLedger(lines(placed("oa1"), { t: new Date(T0 + 1000).toISOString(), ev: "partial", id: "oa1", frac: 0.4, fill_seq: 1 }));
    const o = st.orders.get("oa1")!;
    expect(o.status).toBe("partially_filled");
    expect(o.filled_frac).toBeCloseTo(0.4);
    const rec = recoveryEvents(st, T0 + 2 * K_WINDOW_MS);
    expect(rec.events.map((e) => [e.ev, e.reason])).toEqual([["canceled", "monitor_downtime"]]);
  });

  test("cancel event then restart: order stays canceled and is outside the fill-rate denominator", () => {
    const dir = tmp();
    const c = clock();
    const m1 = createMonitor({ dataDir: dir, now: c.now });
    m1.placeBatch("b", quote);
    m1.shutdown("SIGTERM");
    const m2 = createMonitor({ dataDir: dir, now: c.now });
    const s = m2.stats();
    expect(s.orders.open).toBe(0);
    expect(s.by_symbol.ETHUSDT.canceled).toBe(4);
    expect(s.by_symbol.ETHUSDT.resolved).toBe(0);
    expect(readFileSync(m2.LEDGER, "utf-8")).not.toContain("monitor_downtime");
    expect(s.denominator).toContain("filled + expired");
  });

  test("filled before a crash: AS checks due during downtime become as_missed, later ones stay pending", () => {
    const fill = { t: new Date(T0 + 60_000).toISOString(), ev: "filled", id: "oa1", fill_ms: T0 + 60_000, ttf_s: 60, mid_at_fill: 99_990 };
    const st = replayLedger(lines(placed("oa1"), fill));
    const rec = recoveryEvents(st, T0 + 60_000 + 2 * 3600_000);   // +15m and +1h due, +4h not
    expect(rec.events.map((e) => e.horizon_ms)).toEqual([AS_CHECKS_MS[0], AS_CHECKS_MS[1]]);
    expect(rec.events.every((e) => e.ev === "as_missed")).toBe(true);
    expect(rec.pending.map((p) => p.horizon_ms)).toEqual([AS_CHECKS_MS[2]]);
  });
});

describe("ledger anomalies", () => {
  test("the same event twice is counted once", () => {
    const st = replayLedger(lines(placed("oa1"), placed("oa1"),
                                  { t: "x", ev: "expired", eid: "oa1:expired", id: "oa1" },
                                  { t: "y", ev: "expired", eid: "oa1:expired", id: "oa1" }));
    expect(st.orders.size).toBe(1);
    expect(st.orders.get("oa1")!.status).toBe("expired");
    expect(st.report.duplicates).toBe(2);
    expect(st.report.status).toBe("DUPLICATES_IGNORED");
  });

  test("a duplicate event with different content is a conflict, the order becomes SUSPECT", () => {
    const st = replayLedger(lines(placed("oa1"), { ev: "filled", eid: "oa1:filled", id: "oa1", fill_ms: T0 + 1, mid_at_fill: 1 },
                                  { ev: "filled", eid: "oa1:filled", id: "oa1", fill_ms: T0 + 9, mid_at_fill: 2 }));
    expect(st.report.conflicting_duplicates).toBe(1);
    expect(st.report.status).toBe("CORRUPT");
    const s = cellStats(st.orders.values());
    expect(s.by_symbol.BTCUSDT.suspect).toBe(1);
    expect(s.by_symbol.BTCUSDT.filled).toBe(0);            // suspect orders are not counted as fills
  });

  test("reversed order: fill before placement is reported, not invented", () => {
    const st = replayLedger(lines({ ev: "filled", id: "oa1", fill_ms: T0 + 1, mid_at_fill: 1 }, placed("oa1")));
    expect(st.report.anomalies.map((a) => a.kind)).toContain("OUT_OF_ORDER");
    expect(st.orders.get("oa1")!.status).toBe("open");
    expect(st.report.status).toBe("CORRUPT");
  });

  test("transition after a terminal state is refused", () => {
    const st = replayLedger(lines(placed("oa1"), { ev: "expired", id: "oa1" },
                                  { ev: "filled", id: "oa1", fill_ms: T0 + 1, mid_at_fill: 1 }));
    expect(st.orders.get("oa1")!.status).toBe("expired");
    expect(st.report.anomalies[0].kind).toBe("TRANSITION_AFTER_TERMINAL");
  });

  test("corrupt ledger: unreadable lines are listed, readable orders are still restored", () => {
    const dir = tmp();
    writeFileSync(join(dir, "fill_ledger.jsonl"),
                  lines(placed("oa1")) + "{not json\n" + lines({ ev: "expired", id: "oa1" }) + "\"string\"\n");
    const m = createMonitor({ dataDir: dir, now: () => T0 + 1 });
    const s = m.stats();
    expect(s.ledger_integrity.corrupt_lines).toEqual([2, 4]);
    expect(s.ledger_integrity.status).toBe("CORRUPT");
    expect(s.by_symbol.BTCUSDT.expired).toBe(1);
  });

  test("invalid placed records are not restored", () => {
    const st = replayLedger(lines(placed("oa1", { p_lim: -1 }), placed("ob1", { symbol: "DOGEUSDT" }),
                                  placed("oc1", { expiry_ms: T0 })));
    expect(st.orders.size).toBe(0);
    expect(st.report.anomalies.map((a) => a.kind)).toEqual(["INVALID_ORDER", "INVALID_ORDER", "INVALID_ORDER"]);
  });

  test("legacy ledger without eid builds the same keys", () => {
    expect(eventKey({ id: "oa1", ev: "placed" })).toBe("oa1:placed");
    expect(eventKey({ id: "oa1", ev: "as", horizon_ms: 900000 })).toBe("oa1:as:900000");
    expect(eventKey({ eid: "x", id: "oa1", ev: "placed" })).toBe("x");
  });
});

describe("statistics", () => {
  test("several symbols at once: per-symbol and per-cell counts and denominators", () => {
    const dir = tmp();
    const c = clock();
    const m = createMonitor({ dataDir: dir, now: c.now });
    m.placeBatch("b", quote);
    c.add(1000);
    m.onTrade("ETHUSDT", 3_100, 3_050);                     // ETH shorts fill (price above both limits)
    m.onTrade("BTCUSDT", 99_999.9, 100_000);                // touch only: strict pass required -> no fill
    c.add(K_WINDOW_MS);
    m.housekeeping(() => null);                             // no mid at due time -> as_missed, never 0
    const s = m.stats();
    expect(s.by_symbol.ETHUSDT).toMatchObject({ placed: 4, filled: 2, expired: 2, resolved: 4, fill_rate: 0.5 });
    expect(s.by_symbol.BTCUSDT).toMatchObject({ placed: 4, filled: 0, expired: 4, fill_rate: 0 });
    const ethShort = s.cells.filter((r: any) => r.symbol === "ETHUSDT" && r.side === "short");
    expect(ethShort.map((r: any) => r.filled)).toEqual([1, 1]);
    expect(s.adverse_selection.checks_resolved).toBe(0);
    expect(s.adverse_selection.checks_missed).toBe(6);
  });

  test("open orders are counted, not dropped, and the fill rate uses only finished windows", () => {
    const st = replayLedger(lines(placed("oa1"), placed("ob1"), { ev: "filled", id: "ob1", fill_ms: T0 + 5, ttf_s: 5, mid_at_fill: 1 }));
    const s = cellStats(st.orders.values());
    expect(s.by_symbol.BTCUSDT).toMatchObject({ placed: 2, open: 1, filled: 1, resolved: 1, fill_rate: 1 });
  });

  test("a failed ledger write does not change the in-memory state", () => {
    const dir = tmp();
    let fail = false;
    const m = createMonitor({ dataDir: dir, now: () => T0, append: (p, l) => {
      if (fail) throw new Error("disk full");
      appendFileSync(p, l);
    } });
    fail = true;
    m.placeBatch("b", quote);
    const s = m.stats();
    expect(s.orders.total_tracked).toBe(0);
    expect(s.ledger_write_failures).toBe(8);
  });
});
