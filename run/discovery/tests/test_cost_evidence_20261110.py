"""ADR 0063: only REAL-account fills can verify the cost model; every cost
component is labelled with its treatment and evidence level.

Regression of the 2026-11-02 incident: an OKX DEMO (paper-trading) record was
first booked as a real fill.  ``fills_import`` had no notion of the account
kind, so a demo export imported as ``user_export`` with enough fills would
have produced a VERIFIED record.  Synthetic fills only; nothing is sent.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cost_calibration as cc  # noqa: E402
import fills_import as fi  # noqa: E402
from costs_bridge import COST_COMPONENTS, CostConfigError, CostContext, cost_component_evidence  # noqa: E402
from test_completion import synth_fills  # noqa: E402


def _rec(account_kind, **kw):
    return cc.calibrate_and_verify(synth_fills(), CostContext.futures_default(),
                                   required_symbols=["BTCUSDT"], order_value=10_000.0,
                                   account_kind=account_kind, **kw)


class TestAccountKind(unittest.TestCase):
    def test_real_account_process_still_verifies(self):
        rec = _rec("real")
        self.assertEqual(rec["status"], "VERIFIED", rec["reasons"])
        self.assertEqual(rec["evidence_tiers"], ["REAL_ACCOUNT_FILLS"])
        CostContext.from_verification(cc.seal(rec))      # accepted

    def test_demo_paper_and_undeclared_accounts_never_verify(self):
        for kind in ("demo", "paper", "unknown", None):
            with self.subTest(kind=kind):
                rec = _rec(kind)
                self.assertEqual(rec["status"], "BLOCKED_EXTERNAL_EVIDENCE")
                self.assertTrue(any("not real-account" in r for r in rec["reasons"]))
                self.assertNotIn("REAL_ACCOUNT_FILLS", rec["evidence_tiers"])

    def test_forged_status_on_a_demo_record_is_refused(self):
        rec = _rec("demo")
        rec.update(status="VERIFIED", reasons=[])
        sealed = cc.seal(rec)
        with self.assertRaises(cc.CalibrationError):
            cc.check_record(sealed)
        with self.assertRaises(CostConfigError):
            CostContext.from_verification(sealed)
        self.assertEqual(cc.evidence_status(sealed), "NOT_CALIBRATED")

    def test_record_without_declared_account_is_refused(self):
        rec = _rec("real")
        rec.pop("account_kind")
        with self.assertRaises(cc.CalibrationError):
            cc.check_record(cc.seal(rec))

    def test_unknown_account_kind_value_is_an_error(self):
        with self.assertRaises(cc.CalibrationError):
            _rec("live")
        with self.assertRaises(fi.FillImportError):
            fi.import_fills(Path(__file__), account_kind="live")

    def test_cli_requires_the_account_kind(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
            p = Path(d) / "t.csv"
            p.write_text("x\n")
            with self.assertRaises(SystemExit):
                fi.main(["--fills", str(p), "--out-dir", d])

    def test_evidence_tiers_are_never_promoted(self):
        self.assertEqual(cc.evidence_tier("exchange_fills", "real"), "REAL_ACCOUNT_FILLS")
        self.assertEqual(cc.evidence_tier("exchange_fills", "demo"), "DEMO_ACCOUNT_RECORDS")
        self.assertEqual(cc.evidence_tier("exchange_fills", None), "UNDECLARED_ACCOUNT")
        self.assertEqual(cc.evidence_tier("quote_snapshots", "real"), "PUBLIC_BOOK_SNAPSHOTS")
        self.assertEqual(cc.evidence_tier("paper_fill_monitor", "real"), "PAPER_FILLS")
        self.assertEqual(cc.evidence_tier("ohlcv_only", "real"), "OHLCV")


class TestComponentEvidence(unittest.TestCase):
    def test_every_component_is_listed_with_its_evidence(self):
        tab = cost_component_evidence(CostContext.futures_default())
        self.assertEqual(set(tab), set(COST_COMPONENTS))
        self.assertEqual(tab["taker_fee"]["evidence"], "UNVERIFIED_COST_MODEL")
        for k in ("partial_fill", "fill_latency", "volatility_dependence"):
            self.assertEqual(tab[k]["evidence"], "NOT_MODELLED")
        self.assertEqual(tab["funding"]["evidence"], "PUBLIC_HISTORY")

    def test_assumed_profile_is_labelled_assumed(self):
        tab = cost_component_evidence(CostContext.from_assumed_profile("assumed_public_v1"))
        self.assertEqual(tab["bid_ask_spread"]["evidence"], "ASSUMED_COST_MODEL")


if __name__ == "__main__":
    unittest.main()
