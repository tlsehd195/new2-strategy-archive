"""ADR 0016: preregistered independent window (2020, market-cap tranches).
Synthetic data only — no network, no real window is opened."""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import independent_window as iw  # noqa: E402
from engine_lib import append_jsonl, iter_jsonl, strategy_id  # noqa: E402
from strategy_spec import SPEC_SCHEMA, canonicalize_spec  # noqa: E402
from test_engine import write_funding_csv, write_kline_csv  # noqa: E402
from test_pipeline import hour_spec, synth_bars, synth_funding, verified_fee_cost  # noqa: E402

REAL_HISTORY = DISC / "validation_window_history.jsonl"


def small_prereg() -> dict:
    """The real preregistration, shortened to Jan-Mar 2020 and three alts."""
    pre, _ = iw.load_prereg()
    pre = copy.deepcopy(pre)
    pre["period"].update({"read_start": "2020-01-01", "read_end": "2020-03-31",
                          "entry_start": "2020-02-01", "warmup_days_after_first_bar": 5})
    pre["eligibility"]["min_months"] = 2
    pre["market_cap_order"]["order_ex_btc_eth"] = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    pre["tranches"] = [{"id": "A", "fixed": ["BTCUSDT"], "alts_from_order": 1, "rule": "test"},
                       {"id": "B", "fixed": [], "alts_from_order": 1, "rule": "test"}]
    return pre


def write_symbol(data_dir: Path, sym: str, months, *, seed: int, drift_hours=(0, 1, 2)):
    for m in months:
        y, mo = map(int, m.split("-"))
        end = f"{y + (mo == 12):04d}-{mo % 12 + 1:02d}-01"
        import datetime as dt
        last = (dt.date.fromisoformat(end) - dt.timedelta(days=1)).isoformat()
        b = synth_bars(f"{m}-01", last, seed=seed + mo, drift_hours=drift_hours)
        write_kline_csv(data_dir / "klines" / f"{sym}-15m-{m}.csv", b)
        write_funding_csv(data_dir / "funding" / f"{sym}-fundingRate-{m}.csv",
                          [x for x in synth_funding(b) if b[0].ts <= x[0] <= b[-1].ts])


def members_of(pre, sha, specs, ctx="C2-test"):
    import hashlib
    from engine_lib import canonical_json
    ms = [{"strategy_id": strategy_id(canonicalize_spec(s)), "spec": canonicalize_spec(s)} for s in specs]
    body = {"schema": iw.MEMBERS_SCHEMA, "window_id": pre["window_id"], "prereg_sha256": sha,
            "context_id": ctx, "state_dir": "", "rule": pre["members"]["rule"],
            "members": ms, "excluded_before_test": {}}
    body["members_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return body


class TestOverlap(unittest.TestCase):
    def test_real_preregistration_is_disjoint_from_every_used_window(self):
        pre, _ = iw.load_prereg()
        self.assertEqual(iw.overlap_issues(pre, list(iter_jsonl(REAL_HISTORY))), [])

    def test_read_end_into_selection_data_is_an_issue(self):
        pre, _ = iw.load_prereg()
        pre = copy.deepcopy(pre)
        pre["period"]["read_end"] = "2021-01-05"
        self.assertTrue(any("READ_START" in x for x in iw.overlap_issues(pre, [])))

    def test_locked_window_and_validation_window_overlaps_are_issues(self):
        pre, _ = iw.load_prereg()
        pre = copy.deepcopy(pre)
        pre["period"].update({"read_start": "2024-01-01", "read_end": "2024-04-30", "entry_start": "2024-02-01"})
        iss = iw.overlap_issues(pre, [{"run": "X", "window_keys": ["BTCUSDT:VAL:1695772800000"]}])
        self.assertTrue(any("locked TEST" in x for x in iss))
        self.assertTrue(any("validation window" in x for x in iss))

    def test_tranche_index_counts_other_opened_tranches(self):
        pre, _ = iw.load_prereg()
        h = [{"window_keys": [iw.window_key(pre, "A")]}]
        self.assertEqual(iw.tranche_index(pre, "A", []), 1)
        self.assertEqual(iw.tranche_index(pre, "B", h), 2)


class TestOpen(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.data = self.td / "data"
        months = ["2020-01", "2020-02", "2020-03"]
        write_symbol(self.data, "BTCUSDT", months, seed=1)
        write_symbol(self.data, "AAAUSDT", ["2020-03"], seed=20)          # 1 month: ineligible
        write_symbol(self.data, "BBBUSDT", months, seed=40)
        self.hist = self.td / "history.jsonl"
        for r in iter_jsonl(REAL_HISTORY):
            append_jsonl(self.hist, r)
        self.pre = small_prereg()
        self.sha = "f" * 64
        self.members = members_of(self.pre, self.sha, [hour_spec(0, 2)] + [hour_spec(h, h + 2) for h in (6, 12)])

    def test_tranches_follow_market_cap_order_and_skip_ineligible(self):
        chk = iw.data_check(self.data, self.pre)
        self.assertFalse(chk["AAAUSDT"]["eligible"])
        self.assertTrue(chk["BBBUSDT"]["eligible"])
        tr = iw.resolve_tranches(self.pre, chk)
        self.assertEqual(tr["A"]["symbols"], ["BTCUSDT", "BBBUSDT"])
        self.assertEqual(tr["B"]["symbols"], [])

    def test_open_once_writes_history_first_and_refuses_reuse(self):
        r = iw.open_tranche(self.pre, self.sha, "A", self.data, self.members, self.hist,
                            self.td / "out", verified_fee_cost())
        self.assertEqual(r["symbols"], ["BTCUSDT", "BBBUSDT"])
        self.assertAlmostEqual(r["test"]["alpha"], 0.05 * 6 / 3.141592653589793 ** 2)
        edge = self.members["members"][0]["strategy_id"]
        self.assertEqual(r["outcome"][edge], "PASSED_INDEPENDENT_WINDOW")
        rows = [x for x in iter_jsonl(self.hist) if iw.window_key(self.pre, "A") in x.get("window_keys", [])]
        self.assertEqual(len(rows), 1)
        self.assertTrue((self.td / "out" / "IW1-A_result.json").exists())
        with self.assertRaises(iw.WindowRefused):
            iw.open_tranche(self.pre, self.sha, "A", self.data, self.members, self.hist,
                            self.td / "out2", verified_fee_cost())

    def test_no_bar_after_read_end_is_used(self):
        write_symbol(self.data, "BTCUSDT", ["2020-04"], seed=9)
        e = iw.load_symbol(self.data, "BTCUSDT", self.pre)
        self.assertLessEqual(e["bars"][-1].ts, iw._end_ms("2020-03-31"))
        r = iw.open_tranche(self.pre, self.sha, "A", self.data, self.members, self.hist,
                            self.td / "out", verified_fee_cost())
        self.assertLessEqual(r["data"]["BTCUSDT"]["n_bars"], 91 * 96)

    def test_tampered_or_foreign_members_are_refused_before_any_history_row(self):
        bad = copy.deepcopy(self.members)
        bad["members"][0]["spec"]["exit"]["horizon"] = 16
        n0 = len(list(iter_jsonl(self.hist)))
        with self.assertRaises(iw.WindowRefused):
            iw.open_tranche(self.pre, self.sha, "A", self.data, bad, self.hist, self.td / "o", verified_fee_cost())
        with self.assertRaises(iw.WindowRefused):
            iw.open_tranche(self.pre, "0" * 64, "A", self.data, self.members, self.hist, self.td / "o",
                            verified_fee_cost())
        self.assertEqual(len(list(iter_jsonl(self.hist))), n0)

    def test_members_needing_fng_are_excluded_before_the_test(self):
        fng_spec = {"schema": SPEC_SCHEMA, "direction": "long", "symbols": ["BTCUSDT"],
                    "entry": {"logic": "all", "conditions": [
                        {"feature": "fng_max", "params": {}, "op": "lt", "value": 25}]},
                    "filters": [], "exit": {"horizon": 8}}
        try:
            canonicalize_spec(fng_spec)
        except Exception:  # noqa: BLE001 — feature name differs: nothing to test here
            self.skipTest("no fng feature registered")
        specs = {"S-1": canonicalize_spec(hour_spec(0, 2)), "S-2": canonicalize_spec(fng_spec)}

        class Pipe:
            def validation_candidates(self):
                return ["S-1", "S-2"]

            def context_id(self):
                return "C2-x"

            class specs:  # noqa: N801
                @staticmethod
                def get(s):
                    return specs[s]
        rec = iw.members_record(Pipe(), self.pre, self.sha)
        self.assertEqual([m["strategy_id"] for m in rec["members"]], ["S-1"])
        self.assertIn("S-2", rec["excluded_before_test"])


class TestPower(unittest.TestCase):
    def test_power_bounds_match_the_preregistered_numbers(self):
        pre, _ = iw.load_prereg()
        r = iw.power_precheck(pre)
        self.assertEqual((r["n_days"], r["m"]), (335, 20))
        p2 = r["power_by_annual_sr"]["2"]
        self.assertAlmostEqual(p2["power_best"], 0.516, places=2)
        self.assertAlmostEqual(p2["power_worst"], 0.147, places=2)
        self.assertLess(iw.power_precheck(pre, m=5)["annual_sr_for_power_0.8"]["worst"],
                        r["annual_sr_for_power_0.8"]["worst"])


class TestFetchMirror(unittest.TestCase):
    def test_base_url_is_used_and_recorded(self):
        from data_fetch import S3_MIRROR, fetch_missing
        from test_data_fetch import month_csv, zip_of
        import datetime as dt
        import hashlib
        z = zip_of("BTCUSDT-15m-2020-01.csv", month_csv("2020-01"))
        url = f"{S3_MIRROR}/klines/BTCUSDT/15m/BTCUSDT-15m-2020-01.zip"
        files = {url: z, url + ".CHECKSUM": f"{hashlib.sha256(z).hexdigest()}  x.zip\n".encode()}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_missing(Path(td), ["BTCUSDT"], "2020-01", "2020-01", allow=True, kinds=("klines",),
                              opener=lambda u: files[u], today=dt.date(2026, 10, 21), base=S3_MIRROR)
            self.assertEqual(r["fetched"], 1)
            prov = [json.loads(x) for x in (Path(td) / "_provenance.jsonl").read_text().splitlines()]
            self.assertEqual(prov[0]["url"], url)


if __name__ == "__main__":
    unittest.main()
