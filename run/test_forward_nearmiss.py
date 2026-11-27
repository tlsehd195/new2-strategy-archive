"""Tests for the forward paper record of CL1/FIP1/AF1 (ADR 0112). Synthetic data only."""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import broad_universe_dev as bu  # noqa: E402
import forward_nearmiss as fw  # noqa: E402
import research_data_integrity as rdi  # noqa: E402


def synthetic(data: Path, n_sym: int = 60, seed: int = 0, jitter_ms: int = 0) -> None:
    """Complete synthetic archives: 1d klines, 3 funding settlements a day (8h,
    declared interval column as in the Binance archive), the 23:45 15m volume."""
    rng = np.random.default_rng(seed)
    for d in ("daily", "funding", "bar2345"):
        (data / d).mkdir(parents=True)
    days = np.arange(bu.ms("2026-05-01"), bu.ms("2027-01-01"), bu.DAY_MS)
    for k in range(n_sym):
        sym = f"S{k:02d}USDT"
        c = 10 * np.exp(np.cumsum(rng.normal(0, 0.03, len(days))))
        o = np.r_[c[0], c[:-1]]
        qv = rng.uniform(5e6, 5e7, len(days)) * (1 + k)
        by, fby, bby = {}, {}, {}
        for t, oo, cc, q in zip(days, o, c, qv):
            ym = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m")
            hi, lo = max(oo, cc) * 1.02, min(oo, cc) * 0.98
            by.setdefault(ym, []).append(f"{t},{oo},{hi},{lo},{cc},1,{t + 1},{q},{int(q / 1e4)},1,{q / 2},0")
            for h in (0, 8, 16):
                fby.setdefault(ym, []).append(f"{t + h * 3_600_000 + jitter_ms},8,{rng.normal(1e-4, 1e-4)}")
            bby.setdefault(ym, []).append(f"{t + 85_500_000},{q / 96}")
        for ym, rows in by.items():
            (data / "daily" / f"{sym}-1d-{ym}.csv").write_text("\n".join(rows) + "\n")
            (data / "funding" / f"{sym}-fundingRate-{ym}.csv").write_text(
                "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(fby[ym]) + "\n")
            (data / "bar2345" / f"{sym}-2345-{ym}.csv").write_text("\n".join(bby[ym]) + "\n")


#: ADR 0116 golden values: forward_nearmiss.evaluate on synthetic(seed 0), asof
#: 2027-01-05, computed with the v1 code at main d564456 (0-fill engine).  On
#: complete data the v2 engine must reproduce them exactly.
GOLDEN_V1 = {
    "U50:CL1_CLOSE_LOCATION": (1.886722862, 2.2369643553, 1.5195738007, 2.3145782788, 0.9316755408),
    "U50:FIP1_FROGPAN": (3.3241096057, 3.5569283084, 3.0855183887, 3.8471150239, 1.7949162773),
    "U50:AF1_AGGFUND_TIMING": (-1.0590225344, -0.9109708786, -1.2062346654, -0.9379517006, -0.3621055197),
}


def run_fw(data: Path, asof=dt.date(2027, 1, 5)) -> dict:
    fw.r11._EXTRA.clear()
    rdi.PANEL_LOGS.clear()
    with mock.patch.object(fw, "prereg_commit_time",
                           return_value=dt.datetime.fromisoformat("2026-11-20T12:00:00+00:00")):
        return fw.evaluate(data, asof)


def held_name(out: dict, key: str) -> str:
    """A symbol the candidate holds during the forward period (from a clean run)."""
    return out["_held"][key]


class ForwardNearMissTest(unittest.TestCase):
    def test_prereg_matches_the_code(self):
        p = json.loads(fw.PREREG.read_text())
        self.assertTrue(p["written_before_any_result"])
        self.assertEqual(p["forward_start"], fw.FORWARD_START)
        self.assertEqual(sorted(p["candidates"]), sorted(h for h, _ in fw.KEYS))
        self.assertEqual(p["alpha_family"], fw.ALPHA_FAMILY)

    def test_guard_blocks_outside_forward_reads(self):
        g = fw.guard("2026-12-31")
        with mock.patch.object(bu, "NEVER", g):
            bu.assert_allowed(bu.ms("2026-05-01"), bu.ms("2026-12-31"))
            for lo, hi in (("2026-04-30", "2026-05-02"), ("2027-01-01", "2027-01-02"),
                           ("2020-06-01", "2020-06-02"), ("2024-01-01", "2024-01-02")):
                with self.assertRaises(SystemExit):
                    bu.assert_allowed(bu.ms(lo), bu.ms(hi))
        with self.assertRaises(SystemExit):  # the real DEV guard is untouched afterwards
            bu.assert_allowed(bu.ms("2026-06-01"), bu.ms("2026-06-02"))

    def test_holm_and_stages(self):
        self.assertEqual(fw.holm({"a": 0.001, "b": 0.5}, 0.0213), {"a": True, "b": False})
        rng = np.random.default_rng(1)
        s = {"k": {c: rng.normal(0, 0.01, 100) for c in ("cal", "cal_x2", "cost0")}}
        self.assertEqual(fw.verdicts(s, 100)["stage"], "OBSERVING")
        s = {"k": {c: rng.normal(-0.003, 0.01, 200) for c in ("cal", "cal_x2", "cost0")}}
        self.assertEqual(fw.verdicts(s, 200)["tests"]["k"]["verdict"], "FORWARD_FAIL_FUTILITY")

    def test_end_to_end_synthetic(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            synthetic(data)
            fw.r11._EXTRA.clear()
            with mock.patch.object(fw, "prereg_commit_time",
                                   return_value=dt.datetime.fromisoformat("2026-11-20T12:00:00+00:00")):
                out = fw.evaluate(data, dt.date(2027, 1, 5))
            self.assertEqual(out["read"], ["2026-05-01", "2026-12-31"])
            self.assertEqual(out["stage"], "OBSERVING")
            self.assertGreater(out["forward_days"], 30)
            self.assertEqual(sorted(out["tests"]), sorted(f"{u}:{h}" for h, u in fw.KEYS))
            with self.assertRaises(SystemExit):
                fw.evaluate(data, dt.date(2026, 11, 25))



def strong(n: int, mu: float = 0.004, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    x = rng.normal(mu, 0.01, n)
    return {"cal": x.copy(), "cal_x2": x - 0.0001, "cost0": x + 0.0001}


class ForwardDataIntegrityTest(unittest.TestCase):
    """ADR 0116: missing data never becomes a 0 % day or 0 funding, and no PASS
    is possible on data that are missing, substituted beyond the limit or failed
    a check."""

    def test_complete_data_reproduces_v1_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            synthetic(Path(d))
            out = run_fw(Path(d))
        for key, (sr_, c0, x2, cum, t) in GOLDEN_V1.items():
            row = out["tests"][key]
            self.assertAlmostEqual(row["sr_ann"], sr_, places=9)
            self.assertAlmostEqual(row["sr_cost0"], c0, places=9)
            self.assertAlmostEqual(row["sr_cost_x2"], x2, places=9)
            self.assertAlmostEqual(row["cum_return_pct"], cum, places=9)
            self.assertAlmostEqual(row["nw_t"], t, places=9)
            self.assertEqual(row["data"]["status"], "COMPLETE")
            self.assertEqual(row["data"]["days_invalid"], 0)
            self.assertEqual(row["status"], "INSUFFICIENT_DATA")   # 38 days: observing
        self.assertEqual(out["engine_version"], "newsrc-engine-v2")
        self.assertIn("ESTIMATED_MODEL", out["cost_evidence"])

    def test_settlement_jitter_is_attributed_to_the_held_day(self):
        # the archive's 00:00:00.002 settlement pays for the day before
        self.assertEqual(int(rdi.funding_day(bu.ms("2026-06-02") + 2)), bu.ms("2026-06-01"))
        self.assertEqual(int(rdi.funding_day(bu.ms("2026-06-01") + 8 * 3_600_000 + 5)), bu.ms("2026-06-01"))
        with tempfile.TemporaryDirectory() as d:
            synthetic(Path(d), jitter_ms=4)
            out = run_fw(Path(d))
        for row in out["tests"].values():
            self.assertEqual(row["data"]["days_invalid"], 0)       # jitter is not a gap

    def _held(self, data: Path) -> tuple:
        out = run_fw(data)
        ev = out["volume_provenance"]["U50:AF1_AGGFUND_TIMING"]
        self.assertGreater(ev["legs"], 0)
        return out

    def _drop_daily(self, data: Path, sym: str, day: str) -> None:
        ym = day[:7]
        p = data / "daily" / f"{sym}-1d-{ym}.csv"
        t = str(bu.ms(day))
        p.write_text("".join(ln + "\n" for ln in p.read_text().splitlines() if not ln.startswith(t + ",")))

    def test_missing_price_is_invalid_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            synthetic(data)
            # AF1 is equal-weight U50 when funding is low: remove one forward day
            # of every symbol's open -> every held name has a missing return
            for k in range(60):
                self._drop_daily(data, f"S{k:02d}USDT", "2026-12-10")
            out = run_fw(data)
        self.assertGreater(out["flags"]["missing_return_days"], 0)
        bad = [k for k, r in out["tests"].items() if r["data"]["days_invalid"] > 0]
        self.assertTrue(bad)
        for k in bad:
            ev = out["data_issues"][k]
            self.assertIn("PRICE_RETURN_MISSING", ev["counts"])
            e = [x for x in ev["events"] if x["kind"] == "PRICE_RETURN_MISSING"][0]
            self.assertTrue(e["symbol"].endswith("USDT"))
            self.assertIn(e["day"], ("2026-12-09", "2026-12-10"))
            self.assertEqual(e["series"], k)
            self.assertLess(out["tests"][k]["days_valid"], out["tests"][k]["days"])
            self.assertNotEqual(out["tests"][k]["status"], "PASS")

    def test_missing_funding_settlement_is_invalid_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            synthetic(data)
            t = str(bu.ms("2026-12-10") + 8 * 3_600_000)
            for k in range(60):
                p = data / "funding" / f"S{k:02d}USDT-fundingRate-2026-12.csv"
                p.write_text("".join(ln + "\n" for ln in p.read_text().splitlines() if not ln.startswith(t + ",")))
            out = run_fw(data)
        self.assertGreater(out["flags"]["funding_invalid_name_days"], 0)
        hit = [k for k, v in out["data_issues"].items() if "FUNDING_SETTLEMENT_GAP" in v["counts"]]
        self.assertTrue(hit)
        for k in hit:
            self.assertGreater(out["tests"][k]["data"]["days_invalid"], 0)

    def test_conflicting_duplicate_candle_is_a_panel_issue(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            synthetic(data)
            p = data / "daily" / "S59USDT-1d-2026-12.csv"
            rows = p.read_text().splitlines()
            f = rows[5].split(",")
            f[1] = str(float(f[1]) * 1.5)
            p.write_text("\n".join(rows + [",".join(f)]) + "\n")
            out = run_fw(data)
        self.assertIn("PANEL_DUPLICATE_CONFLICT", out["panel_issues"]["counts"])

    def test_verdict_gate(self):
        n = fw.FINAL_DAYS
        ok = fw.verdicts({"k": strong(n)}, n)
        self.assertEqual(ok["tests"]["k"]["verdict"], "FORWARD_PASS")
        self.assertEqual(ok["tests"]["k"]["status"], "PASS")
        # one unobserved day: no PASS (UNDETERMINED), the verdict is withheld
        s = strong(n)
        for c in s:
            s[c][100] = np.nan
        r = fw.verdicts({"k": s}, n)["tests"]["k"]
        self.assertEqual(r["status"], "UNDETERMINED")
        self.assertEqual(r["verdict"], "WITHHELD_UNDETERMINED")
        self.assertEqual(r["verdict_withheld"], "FORWARD_PASS")
        # more than 2 % unobserved: INVALID_DATA
        s = strong(n)
        for c in s:
            s[c][:10] = np.nan
        r = fw.verdicts({"k": s}, n)["tests"]["k"]
        self.assertEqual(r["status"], "INVALID_DATA")
        # estimated volume above the limit: UNDETERMINED even with every day observed
        g = fw.data_gate(n, 0, {"estimated_share": 0.5}, 0)
        r = fw.verdicts({"k": strong(n)}, n, {"k": g})["tests"]["k"]
        self.assertEqual(r["status"], "UNDETERMINED")
        # a panel problem on a held name: INVALID_DATA
        g = fw.data_gate(n, 0, {"estimated_share": 0.0}, 1)
        r = fw.verdicts({"k": strong(n)}, n, {"k": g})["tests"]["k"]
        self.assertEqual(r["status"], "INVALID_DATA")
        # too short: INSUFFICIENT_DATA, never PASS
        r = fw.verdicts({"k": strong(40)}, 40)["tests"]["k"]
        self.assertEqual((r["verdict"], r["status"]), ("OBSERVING", "INSUFFICIENT_DATA"))
        # a real failure on observed days stays a FAIL
        s = strong(n, mu=-0.004)
        r = fw.verdicts({"k": s}, n)["tests"]["k"]
        self.assertEqual((r["verdict"], r["status"]), ("FORWARD_FAIL", "FAIL"))
        # non-finite statistics at the final stage: ERROR
        z = {c: np.zeros(n) for c in ("cal", "cal_x2", "cost0")}
        r = fw.verdicts({"k": z}, n)["tests"]["k"]
        self.assertEqual(r["status"], "ERROR")
        for st in (ok["tests"]["k"]["status"], "FAIL", "ERROR"):
            self.assertIn(st, fw.STATUSES)

    def test_invalid_candidate_cannot_ease_holm_for_the_others(self):
        n = fw.FINAL_DAYS
        good = strong(n, mu=0.002, seed=5)
        bad = strong(n, mu=0.01, seed=6)
        for c in bad:
            bad[c][:30] = np.nan
        out = fw.verdicts({"a": good, "b": bad, "c": strong(n, mu=0.0, seed=7)}, n)
        self.assertEqual(out["tests"]["b"]["p_one_sided"], 1.0)
        self.assertEqual(out["tests"]["b"]["status"], "INVALID_DATA")
        self.assertFalse(out["holm_reject"]["b"])

    def test_addendum_states_no_rule_change(self):
        a = json.loads(fw.ADDENDUM.read_text())
        self.assertEqual(a["prereg_sha256"], bu.sha(fw.PREREG))
        self.assertFalse(a["changes_candidates_parameters_universe_cost_or_statistics"])
        self.assertEqual(a["data_policy"], rdi.POLICY)
        repo = Path(__file__).resolve().parent.parent
        for f, h in a["code_sha256"].items():   # the addendum names the code that computes the record
            self.assertEqual(bu.sha(repo / f), h, f"{f} changed: re-run run/audit/adr0116/write_forward_addendum.py")


class ImpactVolumeProvenanceTest(unittest.TestCase):
    def test_observed_estimated_missing(self):
        import pandas as pd
        qv = pd.DataFrame({"A": [1e6, 2e6, np.nan], "B": [np.nan, np.nan, np.nan]})
        self.assertEqual(rdi.impact_volume({("A", 7): 5.0}, qv, "A", 2, 7), (5.0, "OBSERVED_BAR2345"))
        self.assertEqual(rdi.impact_volume({}, qv, "A", 2, 7), (2e6 / 96, "ESTIMATED_DAILY_96"))
        qv.loc[1, "A"] = np.nan
        self.assertEqual(rdi.impact_volume({}, qv, "A", 2, 7), (1e6 / 96, "ESTIMATED_MEAN30_96"))
        self.assertEqual(rdi.impact_volume({}, qv, "B", 2, 7), (None, "MISSING"))   # old code: 1.0
        vp = rdi.VolumeProvenance()
        for k in ("OBSERVED_BAR2345", "OBSERVED_BAR2345", "ESTIMATED_DAILY_96", "MISSING"):
            vp.add(k)
        j = vp.to_json()
        self.assertEqual((j["legs"], j["observed_share"], j["estimated_share"]), (4, 0.5, 0.25))

if __name__ == "__main__":
    unittest.main()
