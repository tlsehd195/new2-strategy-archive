"""Directive 2026-10-20 §4 — importing the user's exported fills, SYNTHETIC data only.

Every file here is generated in a temp dir and imported with
``data_origin="synthetic_test"``: it can exercise every check, and it can
never produce VERIFIED_FROM_ACTUAL_FILLS.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
REPO = DISC.parent.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cost_calibration as cc  # noqa: E402
import fills_import as fi  # noqa: E402
from data_quality import Bar  # noqa: E402

T0 = 1_760_000_000_000 - (1_760_000_000_000 % 900_000)       # 2025-10-09, on a 15m boundary
NOW = T0 + 400 * 86_400_000
HEADER = ["Date(UTC)", "Symbol", "Side", "Price", "Quantity", "Amount", "Fee", "Fee Coin",
          "Order ID", "Trade ID", "Maker"]
MID = {"BTCUSDT": 100.0, "ETHUSDT": 50.0}


def _utc(ts_ms: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def synth_market(n_orders: int, *, symbols=("BTCUSDT", "ETHUSDT"), fee=0.0004, half=0.0001, k=0.05,
                 seed=11, fee_asset="USDT", bias_bp=0.0):
    """A KNOWN cost process: (csv rows, decision log, klines dict).  One taker
    order per 30 minutes per symbol, filled in one or two trades."""
    rng = random.Random(seed)
    rows, decisions, bars = [], [], {s: [] for s in symbols}
    tid = 1000
    for i in range(n_orders):
        for s in symbols:
            bt = T0 + i * 1_800_000
            qv = rng.uniform(5e6, 5e7)
            mid = MID[s]
            bars[s].append(Bar(ts=bt, open=mid, high=mid * 1.01, low=mid * 0.99, close=mid,
                               volume=qv / mid, quote_volume=qv))
            bars[s].append(Bar(ts=bt + 900_000, open=mid, high=mid * 1.01, low=mid * 0.99, close=mid,
                               volume=qv / mid, quote_volume=qv))
            dts = bt + 900_000 + 1_000                      # decision just after the bar at bt closed
            side = rng.choice((1, -1))
            nv = rng.uniform(5_000.0, 20_000.0)
            # bias_bp: the execution gets dearer in the later (verification) half only
            slip = half + k * math.sqrt(nv / qv) + rng.gauss(0, 0.00002) + (bias_bp / 1e4 if i >= n_orders // 2 else 0.0)
            px = round(mid * (1 + side * slip), 6)
            oid = f"{s[:1]}{i}"
            parts = [nv] if rng.random() < 0.5 else [nv * 0.4, nv * 0.6]
            for j, part in enumerate(parts):
                qty = part / px
                tid += 1
                rows.append({"Date(UTC)": _utc(dts + 200 + j), "Symbol": s,
                             "Side": "BUY" if side == 1 else "SELL", "Price": f"{px}",
                             "Quantity": f"{qty:.10f}", "Amount": f"{px * qty:.8f}",
                             "Fee": f"{fee * part:.10f}" if fee_asset == "USDT" else f"{fee * part / 500:.10f}",
                             "Fee Coin": fee_asset, "Order ID": oid, "Trade ID": str(tid), "Maker": "false"})
            decisions.append({"order_id": oid, "symbol": s, "decision_ts_ms": dts,
                              "bid": mid * (1 - half), "ask": mid * (1 + half),
                              "requested_notional": nv, "side": "BUY" if side == 1 else "SELL",
                              "order_type": "taker", "status": "FILLED"})
    kl = {s: (sorted(b, key=lambda x: x.ts), [x.ts for x in sorted(b, key=lambda x: x.ts)]) for s, b in bars.items()}
    return rows, decisions, kl


def write_csv(path: Path, rows, header=HEADER):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


class ImportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def imp(self, rows, header=HEADER, **kw):
        p = self.d / "trades.csv"
        write_csv(p, rows, header)
        kw.setdefault("data_origin", "synthetic_test")
        return fi.import_fills(p, now_ms=NOW, **kw)


class TestColumnsTypesAndMissing(ImportCase):
    def test_clean_export_is_imported_sorted_with_counts_and_period(self):
        rows, _, _ = synth_market(30)
        res = self.imp(list(reversed(rows)))
        self.assertEqual(res["status"], "IMPORTED")
        rep = res["report"]
        self.assertFalse(rep["input_sorted"])
        ts = [f["ts_ms"] for f in res["fills"]]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(rep["valid_fills"], len(rows))
        self.assertEqual(set(rep["per_symbol"]), {"BTCUSDT", "ETHUSDT"})
        self.assertEqual(rep["per_symbol"]["BTCUSDT"]["orders"], 30)
        self.assertGreater(rep["per_symbol"]["BTCUSDT"]["days"], 0)
        f = res["fills"][0]
        for k in ("exchange", "symbol", "ts_ms", "side", "price", "qty", "order_id", "trade_id",
                  "liquidity", "fee", "fee_asset", "source_sha256", "imported_at"):
            self.assertIn(k, f)
        self.assertEqual(f["liquidity"], "TAKER")

    def test_missing_required_column_is_data_invalid(self):
        rows, _, _ = synth_market(5)
        res = self.imp(rows, header=[h for h in HEADER if h != "Trade ID"])
        self.assertEqual(res["status"], "DATA_INVALID")
        self.assertIn("trade_id", res["report"]["reason"])

    def test_missing_fee_is_quarantined_never_zero(self):
        rows, _, _ = synth_market(10)
        rows[3]["Fee"] = ""
        res = self.imp(rows)
        self.assertEqual(res["status"], "IMPORTED_WITH_QUARANTINE")
        self.assertEqual([b["row"] for b in res["quarantined"]], [4])
        self.assertIn("fee missing", res["quarantined"][0]["reason"])
        self.assertNotIn(rows[3]["Trade ID"], {f["trade_id"] for f in res["fills"]})

    def test_non_numeric_and_non_positive_values(self):
        rows, _, _ = synth_market(10)
        rows[1]["Price"] = "abc"
        rows[2]["Quantity"] = "0"
        rows[5]["Side"] = "HOLD"
        res = self.imp(rows)
        reasons = {b["row"]: b["reason"] for b in res["quarantined"]}
        self.assertIn("not a number", reasons[2])
        self.assertIn("quantity 0 <= 0", reasons[3])
        self.assertIn("BUY/SELL", reasons[6])

    def test_api_json_format(self):
        rows, _, _ = synth_market(4)
        api = [{"symbol": r["Symbol"], "id": int(r["Trade ID"]), "orderId": r["Order ID"], "side": r["Side"],
                "price": r["Price"], "qty": r["Quantity"], "quoteQty": r["Amount"], "commission": r["Fee"],
                "commissionAsset": r["Fee Coin"], "time": fi._iso_ms(r["Date(UTC)"].replace(" ", "T") + "Z"),
                "maker": False, "buyer": r["Side"] == "BUY", "realizedPnl": "0"} for r in rows]
        p = self.d / "trades.json"
        p.write_text(json.dumps(api))
        res = fi.import_fills(p, now_ms=NOW, data_origin="synthetic_test")
        self.assertEqual(res["report"]["source_format"], "binance_api_json")
        self.assertEqual((res["status"], len(res["fills"])), ("IMPORTED", len(rows)))
        self.assertTrue(all(f["liquidity"] == "TAKER" for f in res["fills"]))


class TestTimeHandling(ImportCase):
    def test_naive_time_needs_a_declared_zone(self):
        rows, _, _ = synth_market(3)
        hdr = ["Time"] + HEADER[1:]
        rows2 = [{**{k: v for k, v in r.items() if k != "Date(UTC)"}, "Time": r["Date(UTC)"]} for r in rows]
        res = self.imp(rows2, header=hdr)
        self.assertEqual(res["status"], "DATA_INVALID")             # every row refused: no guessing
        self.assertTrue(all("pass --timezone" in b["reason"] for b in res["quarantined"]))
        res = self.imp(rows2, header=hdr, timezone="+09:00")
        utc = self.imp(rows)
        self.assertEqual([f["ts_ms"] + 9 * 3_600_000 for f in res["fills"]],
                         [f["ts_ms"] for f in utc["fills"]])

    def test_seconds_instead_of_milliseconds(self):
        rows, _, _ = synth_market(3)
        rows[0]["Date(UTC)"] = str(T0 // 1000)
        res = self.imp(rows)
        self.assertIn("seconds", res["quarantined"][0]["reason"])

    def test_time_outside_plausible_range(self):
        rows, _, _ = synth_market(3)
        rows[0]["Date(UTC)"] = "2018-01-01 00:00:00"
        res = self.imp(rows)
        self.assertIn("outside", res["quarantined"][0]["reason"])


class TestDuplicatesAndAbnormal(ImportCase):
    def test_exact_duplicate_is_quarantined(self):
        rows, _, _ = synth_market(10)
        res = self.imp(rows + [dict(rows[4])])
        self.assertEqual(res["status"], "IMPORTED_WITH_QUARANTINE")
        self.assertEqual(res["report"]["exact_duplicates"], 1)
        self.assertEqual(len(res["fills"]), len(rows))

    def test_conflicting_duplicate_makes_the_file_invalid(self):
        rows, _, _ = synth_market(10)
        twin = dict(rows[4], Price=str(float(rows[4]["Price"]) * 1.001))
        res = self.imp(rows + [twin])
        self.assertEqual(res["status"], "DATA_INVALID")
        self.assertEqual(res["report"]["conflicting_duplicates"], 1)
        self.assertNotIn(rows[4]["Trade ID"], {f["trade_id"] for f in res["fills"]})   # both copies out

    def test_price_in_another_unit_and_quote_qty_mismatch(self):
        rows, _, _ = synth_market(20)
        r = rows[10]
        rows[10] = dict(r, Price=str(float(r["Price"]) * 100), Amount=str(float(r["Price"]) * 100 * float(r["Quantity"])))
        rows[12] = dict(rows[12], Amount=str(float(rows[12]["Amount"]) * 2))
        res = self.imp(rows)
        reasons = {b["row"]: b["reason"] for b in res["quarantined"]}
        self.assertIn("median of neighbouring", reasons[11])
        self.assertIn("quote_qty", reasons[13])

    def test_price_outside_the_public_bar(self):
        rows, _, kl = synth_market(10)
        rows[2] = dict(rows[2], Price=str(MID[rows[2]["Symbol"]] * 1.03),
                       Amount=str(MID[rows[2]["Symbol"]] * 1.03 * float(rows[2]["Quantity"])))
        p = self.d / "t.csv"
        write_csv(p, rows)
        pol, _ = fi.load_import_policy()
        res = fi.import_fills(p, now_ms=NOW, data_origin="synthetic_test")
        good, bad, _ = fi.check_fills(
            fi.normalise_rows(fi._read_rows(p.read_bytes(), "binance_csv")[0], HEADER, policy=pol)[0],
            policy=pol, klines=kl, now_ms=NOW)
        self.assertTrue(any("outside the public 15m bar" in b["reason"] for b in bad))
        self.assertEqual(res["status"], "IMPORTED")       # without klines that check is not possible

    def test_fee_unit_error_and_negative_fee(self):
        rows, _, _ = synth_market(10)
        rows[1] = dict(rows[1], Fee=str(float(rows[1]["Amount"]) * 0.05))      # 5% of notional
        rows[2] = dict(rows[2], Fee="-0.1")
        res = self.imp(rows)
        reasons = {b["row"]: b["reason"] for b in res["quarantined"]}
        self.assertIn("unit error", reasons[2])
        self.assertIn("negative fee", reasons[3])


class TestFeeAssets(ImportCase):
    def test_other_fee_asset_without_a_price_is_not_evidence(self):
        rows, dec, kl = synth_market(10, fee_asset="BNB")
        res = self.imp(rows)
        self.assertEqual(res["report"]["fee_assets"]["unconverted"], len(rows))
        self.assertTrue(all(f["fee_quote"] is None for f in res["fills"]))
        obs, why = fi.orders_to_observations(res["fills"], policy=res["policy"],
                                             decisions={d["order_id"]: d for d in dec}, klines=kl)
        self.assertEqual(obs, [])
        self.assertEqual(why, {"fee not convertible to the quote asset": 20})

    def test_supplied_price_converts_with_method_and_time(self):
        rows, _, _ = synth_market(10, fee_asset="BNB")
        fp = self.d / "fee_prices.jsonl"
        fp.write_text("".join(json.dumps({"asset": "BNB", "ts_ms": T0 + i * 900_000, "price": 500.0,
                                          "quote": "USDT", "source": "test series"}) + "\n"
                              for i in range(0, 60)))
        res = self.imp(rows, fee_prices=fp)
        self.assertEqual(res["report"]["fee_assets"]["converted"], len(rows))
        f = res["fills"][0]
        self.assertAlmostEqual(f["fee_quote"], float(f["fee"]) * 500.0)
        self.assertEqual(f["fee_conversion"]["source"], "test series")
        self.assertLessEqual(f["fee_conversion"]["price_ts_ms"], f["ts_ms"])

    def test_stale_conversion_price_is_not_used(self):
        rows, _, _ = synth_market(3, fee_asset="BNB")
        fp = self.d / "fee_prices.jsonl"
        fp.write_text(json.dumps({"asset": "BNB", "ts_ms": T0 - 3_600_000, "price": 500.0,
                                  "quote": "USDT", "source": "old"}) + "\n")
        res = self.imp(rows, fee_prices=fp)
        self.assertEqual(res["report"]["fee_assets"]["converted"], 0)


class TestVerificationFromImport(ImportCase):
    def test_minimum_sample_rule_is_unchanged(self):
        crit, _ = cc.load_criteria()
        self.assertEqual(crit["min_verification_fills_per_symbol"], 200)

    def test_trades_only_export_cannot_verify(self):
        """no decision log -> no reference quote -> no observation; with quotes
        but without the order log the reject rate is unobservable."""
        rows, dec, kl = synth_market(450)
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0)
        self.assertEqual(rec["status"], "BLOCKED_EXTERNAL_EVIDENCE")
        self.assertEqual(rec["orders_not_used"], {"no reference quote at decision time": 900})
        q = self.d / "quotes.jsonl"
        q.write_text("".join(json.dumps({"symbol": d["symbol"], "ts_ms": d["decision_ts_ms"] - 10,
                                         "bid": d["bid"], "ask": d["ask"]}) + "\n" for d in dec))
        rec = fi.verify_from_import(res, symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0, quotes=q)
        self.assertNotEqual(rec["status"], "VERIFIED")
        self.assertIsNone(rec["fill_rates"]["REJECTED"])
        self.assertTrue(any("reject rate not observable" in r for r in rec["reasons"]))
        self.assertEqual(rec["reference_source"], "quote file")

    def test_full_chain_passes_every_check_but_synthetic_is_never_verified(self):
        rows, dec, kl = synth_market(450)
        dp = self.d / "decisions.jsonl"
        dp.write_text("".join(json.dumps(d) + "\n" for d in dec))
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0, decisions=dp)
        self.assertEqual(rec["status"], "NOT_VERIFIED")
        self.assertEqual(rec["reasons"], ["synthetic test data is never verification evidence"])
        self.assertEqual(rec["scope"]["symbols"], ["BTCUSDT", "ETHUSDT"])      # every other check passed
        self.assertGreaterEqual(min(rec["scope"]["verification_fills_per_symbol"].values()), 200)
        self.assertEqual(rec["data_origin"], "synthetic_test")
        self.assertEqual(cc.evidence_status(cc.seal(rec)), "NOT_VERIFIED")
        forged = cc.seal({**rec, "status": "VERIFIED", "reasons": []})
        with self.assertRaises(cc.CalibrationError):
            cc.check_record(forged)

    def test_underpriced_symbol_fails_and_small_sample_is_insufficient(self):
        rows, dec, kl = synth_market(450, bias_bp=3.0)
        dp = self.d / "decisions.jsonl"
        dp.write_text("".join(json.dumps(d) + "\n" for d in dec))
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0, decisions=dp)
        self.assertEqual(rec["status"], "NOT_VERIFIED")
        self.assertTrue(any("underestimate" in r for r in rec["reasons"]))
        rows, dec, kl = synth_market(150)
        dp.write_text("".join(json.dumps(d) + "\n" for d in dec))
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT", "ETHUSDT"], order_value=10_000.0, decisions=dp)
        self.assertTrue(any("verification fills < 200" in r for r in rec["sample_shortfall"]))

    def test_quarantined_rows_make_the_record_data_invalid(self):
        rows, dec, kl = synth_market(60)
        rows[0]["Fee"] = ""
        dp = self.d / "decisions.jsonl"
        dp.write_text("".join(json.dumps(d) + "\n" for d in dec))
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT"], order_value=10_000.0, decisions=dp)
        self.assertEqual(rec["status"], "DATA_INVALID")
        self.assertEqual(cc.evidence_status(rec), "DATA_INVALID")

    def test_unfilled_orders_from_the_decision_log_count_as_rejects(self):
        rows, dec, kl = synth_market(20, symbols=("BTCUSDT",))
        dec.append({"order_id": "X1", "symbol": "BTCUSDT", "decision_ts_ms": T0 + 5_000, "ref_price": 100.0,
                    "requested_notional": 10_000.0, "status": "REJECTED", "side": "BUY"})
        dp = self.d / "decisions.jsonl"
        dp.write_text("".join(json.dumps(d) + "\n" for d in dec))
        res = self.imp(rows)
        res["klines"] = kl
        rec = fi.verify_from_import(res, symbols=["BTCUSDT"], order_value=10_000.0, decisions=dp)
        self.assertAlmostEqual(rec["fill_rates"]["REJECTED"], 1 / 21)


class TestEvidenceStatusVocabulary(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(cc.evidence_status(None, cost_status="ESTIMATED_FROM_PUBLIC_DATA"),
                         "ESTIMATED_FROM_PUBLIC_QUOTES")
        self.assertEqual(cc.evidence_status(None, cost_status="UNVERIFIED_COST_MODEL"), "NOT_CALIBRATED")
        self.assertEqual(cc.evidence_status(None, cost_status="ASSUMED_COST_MODEL"), "NOT_CALIBRATED")
        self.assertEqual(cc.evidence_status({"status": "BLOCKED_EXTERNAL_EVIDENCE"}), "INSUFFICIENT_DATA")
        self.assertEqual(cc.evidence_status({"status": "INSUFFICIENT_DATA"}), "INSUFFICIENT_DATA")

    def test_verified_record_of_another_cost_source_requires_reverification(self):
        from test_completion import synth_fills
        from costs_bridge import CostContext
        rec = cc.seal(cc.calibrate_and_verify(synth_fills(n=900, symbols=("BTCUSDT", "ETHUSDT")),
                                              CostContext.futures_default(),
                                              required_symbols=["BTCUSDT"], order_value=10_000.0))
        self.assertEqual(rec["status"], "VERIFIED")
        self.assertEqual(cc.evidence_status(rec), "VERIFIED_FROM_ACTUAL_FILLS")
        old = cc.seal({k: v for k, v in {**rec, "cost_source_sha256": "0" * 64}.items()
                       if k not in ("record_id", "sealed_at")})
        self.assertEqual(cc.evidence_status(old), "REVERIFY_REQUIRED")
        self.assertEqual(cc.evidence_status({**rec, "params": {"fee_rate": 0}}), "DATA_INVALID")


class TestPrivacy(ImportCase):
    def test_cli_prints_counts_not_rows_and_writes_under_out_dir(self):
        rows, _, _ = synth_market(10)
        p = self.d / "trades.csv"
        write_csv(p, rows)
        out = self.d / "private"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fi.main(["--fills", str(p), "--out-dir", str(out), "--data-origin", "synthetic_test"])
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        for r in rows[:5]:
            self.assertNotIn(r["Trade ID"], text)
            self.assertNotIn(r["Amount"], text)
        self.assertTrue(any(out.glob("fills_*.jsonl")))
        self.assertIn(f'"valid_fills": {len(rows)}', text)

    def test_private_fill_directory_is_git_ignored(self):
        self.assertIn("run/discovery/private_fills/", (REPO / ".gitignore").read_text())


if __name__ == "__main__":
    unittest.main()
