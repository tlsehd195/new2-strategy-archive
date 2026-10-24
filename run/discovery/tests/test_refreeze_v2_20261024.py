"""IW1 refreeze draft and prereg v2 final-form draft (ADR 0036).

The v2 members freeze picks the first candidates that pass the entry bar in
validation_candidates() order, refuses scores from another basket or cost, and
writes a file that prereg_gate's freeze check accepts.  The new drafts stay
DRAFT, keep the decided v2 numbers, and bind the files they supersede by
sha256.  Committed files and temp data only: no window is opened, no market
data is read."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
REPO = DISC.parent.parent
for p in (str(DISC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import forward_window_freeze as fw  # noqa: E402
import prereg_gate as pg  # noqa: E402
from public_cost_estimate import load_criteria  # noqa: E402

EV = REPO / "run" / "audit" / "evidence_refreeze_v2_20261024"
V2_FINAL = EV / "forward_window_v2_FINALFORM_DRAFT.json"
V2_OLD = REPO / "run" / "audit" / "evidence_prereg_v2_20261021" / "forward_window_v2_DRAFT.json"
IW1_V2 = REPO / "run" / "audit" / "evidence_paper_faithful_20261022" / "independent_window_v2_DRAFT.json"
IW1_REFREEZE = EV / "IW1_members_refreeze_DRAFT.json"
OLD_MEMBERS = REPO / "run" / "audit" / "evidence_independent_window_20261021" / "IW1_members.json"
CRIT_V2 = DISC / "config" / "cost_public_estimate_v2_alts.json"
CRIT_V3 = EV / "cost_public_estimate_v3_alts_DRAFT.json"
CRIT_V3_CONFIG = DISC / "config" / "cost_public_estimate_v3_alts.json"
CRIT_V3_CONFIG_SHA256 = "e11e1ccc4e34a030e0ab0e2ee4e833aa4e9f18acb674e06222d701fc057d8da6"
V2_R2 = EV / "forward_window_v2_FINALFORM_DRAFT_r2.json"
#: symbols of the order-book collection running on the user's PC (2026-10-23..27)
PC_COLLECTION = {"SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
                 "SUIUSDT", "LTCUSDT", "DOTUSDT"}


def _spec():
    return json.loads(OLD_MEMBERS.read_text())["members"][0]["spec"]


def _specs(n):
    """n distinct canonical specs (different horizons of one real member)."""
    from engine_lib import strategy_id
    from strategy_spec import canonicalize_spec
    out = {}
    for h in range(n):
        s = copy.deepcopy(_spec())
        s["exit"]["horizon"] = 8 + 4 * h
        s["primary_cell"]["horizon"] = s["exit"]["horizon"]
        s = canonicalize_spec(s)
        out[strategy_id(s)] = s
    return out


def _scores(pre, ids, s1):
    return {"cost": pre["cost"]["profile"], "symbols": list(pre["basket"]["symbols"]),
            "candidates": [{"strategy_id": i, "S1": {"sr_ann": v}, "S2": {"sr_ann": 0.1}}
                           for i, v in zip(ids, s1)]}


class TestSelect(unittest.TestCase):
    def test_first_n_in_candidate_order(self):
        ids = list("abcdefg")
        sc = {"candidates": [{"strategy_id": i, "S1": {"sr_ann": v}} for i, v in
                             zip(ids, [1.2, 0.4, 1.0, 2.0, 0.99, 1.1, 1.5])]}
        r = fw.select_members(ids, sc, n=3, bar=1.0)
        self.assertEqual(r["picked"], ["a", "c", "d"])           # order kept, not re-ranked by SR
        self.assertTrue(r["registrable"])
        self.assertEqual([c["strategy_id"] for c in r["considered"]], list("abcd"))

    def test_too_few_is_not_registrable(self):
        sc = {"candidates": [{"strategy_id": i, "S1": {"sr_ann": 0.5}} for i in "abc"]}
        r = fw.select_members(list("abc"), sc, n=2, bar=1.0)
        self.assertEqual(r["picked"], [])
        self.assertFalse(r["registrable"])

    def test_nan_and_unscored(self):
        sc = {"candidates": [{"strategy_id": "a", "S1": {"sr_ann": float("nan")}},
                             {"strategy_id": "c", "S1": {"sr_ann": 3.0}}]}
        r = fw.select_members(["a", "b", "c"], sc, n=1, bar=1.0)
        self.assertEqual(r["picked"], ["c"])
        self.assertEqual(r["unscored"], ["b"])
        self.assertFalse(r["registrable"])                       # a missing score is a finding


class TestMembersRecord(unittest.TestCase):
    def setUp(self):
        self.pre, self.sha = fw.load_prereg(V2_FINAL)
        self.specs = _specs(7)
        self.ids = list(self.specs)

    def _rec(self, s1, **kw):
        sc = kw.pop("scores", None) or _scores(self.pre, self.ids, s1)
        return fw.members_record(self.pre, self.sha, candidate_ids=self.ids, specs=self.specs,
                                 context_id="C2-test", scores=sc, scores_sha="s" * 64, **kw)

    def test_passes_gate_freeze_check(self):
        rec = self._rec([1.5, 0.2, 1.1, 1.0, 3.0, 1.2, 1.3])
        self.assertTrue(rec["registrable"])
        self.assertEqual([m["strategy_id"] for m in rec["members"]],
                         [self.ids[i] for i in (0, 2, 3, 4, 5)])
        self.assertEqual(pg.check_members_file(rec, self.sha), [])
        bad = copy.deepcopy(rec)
        bad["members"][0]["spec"]["exit"]["horizon"] += 1
        self.assertTrue(pg.check_members_file(bad, self.sha))
        self.assertTrue(pg.check_members_file(rec, "0" * 64))     # bound to this prereg only

    def test_d5_not_registrable(self):
        rec = self._rec([1.5, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        self.assertFalse(rec["registrable"])
        self.assertEqual(len(rec["members"]), 1)

    def test_refuses_other_basket_or_cost(self):
        sc = _scores(self.pre, self.ids, [2.0] * 7)
        for k, v in (("symbols", sc["symbols"][:9]), ("cost", "public_estimate:x.json")):
            with self.assertRaises(fw.FreezeRefused):
                self._rec(None, scores={**sc, k: v})

    def test_refuses_non_v2_prereg(self):
        with self.assertRaises(fw.FreezeRefused):
            fw.load_prereg(DISC / "config" / "independent_window_v1.json")


class TestDrafts(unittest.TestCase):
    def test_v2_finalform_is_draft_with_all_fields(self):
        rep = pg.evaluate(V2_FINAL)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertTrue(rep["draft_marks"])                      # the status line keeps it a draft
        self.assertEqual(rep["required_missing"], [])
        self.assertEqual(rep["issues"], [])
        self.assertEqual(rep["schema"], "forward_window_prereg_v2")

    def test_v2_finalform_keeps_decided_numbers(self):
        new, old = json.loads(V2_FINAL.read_text()), json.loads(V2_OLD.read_text())
        self.assertEqual(new["supersedes"]["sha256"], hashlib.sha256(V2_OLD.read_bytes()).hexdigest())
        for k in ("test", "window", "decided", "single_use", "family"):
            self.assertEqual(new[k], old[k], k)
        self.assertEqual(new["test"]["alpha"], 0.0213)
        self.assertEqual(new["basket"]["symbols"], old["basket"]["symbols"])
        self.assertEqual(new["cost"]["profile"], "assumed_public_v1")
        self.assertEqual(new["members"]["max"], 5)
        self.assertEqual(new["members"]["entry_bar_s1_sr"], 1.0)
        self.assertTrue(all(q["label"] == "ALT_COST_DEPENDENT" for q in new["pending_at_freeze"].values()))

    def test_measured_cost_cannot_fire_under_v2_criteria(self):
        """The fact behind Q1: the running collection misses basket symbols."""
        crit = load_criteria(CRIT_V2)[0]
        self.assertTrue(set(crit["required_symbols"]) - PC_COLLECTION)

    def test_v3_alt_criteria_draft(self):
        v3, _ = load_criteria(CRIT_V3)
        v2 = json.loads(CRIT_V2.read_text())
        diff = {k for k in set(v2) | set(v3) if v2.get(k) != v3.get(k)}
        self.assertEqual(diff, {"note", "required_symbols", "status"})
        self.assertTrue(set(v3["required_symbols"]) <= PC_COLLECTION)
        self.assertIn("DRAFT", v3["status"])

    def test_v3_alt_criteria_config_q1b(self):
        """Q1-B (ADR 0037): the config equals the reviewed draft except the
        notes, and is pinned by sha256 (a change needs a new version file)."""
        cfg, sha = load_criteria(CRIT_V3_CONFIG)
        draft = json.loads(CRIT_V3.read_text())
        notes = {"note", "status", "supersedes_for_v2"}
        self.assertEqual({k: v for k, v in cfg.items() if k not in notes},
                         {k: v for k, v in draft.items() if k not in notes})
        self.assertEqual(sha, CRIT_V3_CONFIG_SHA256)
        self.assertNotIn("DRAFT", json.dumps(cfg))

    def test_v2_r2_liquid9(self):
        r1, r2 = json.loads(V2_FINAL.read_text()), json.loads(V2_R2.read_text())
        rep = pg.evaluate(V2_R2)
        self.assertEqual(rep["lifecycle"], "DRAFT")
        self.assertEqual((rep["required_missing"], rep["issues"]), ([], []))
        self.assertEqual(r2["supersedes"]["sha256"], hashlib.sha256(V2_FINAL.read_bytes()).hexdigest())
        self.assertEqual(r2["basket"]["symbols"], ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
                                                    "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT"])
        self.assertTrue(set(r2["basket"]["symbols"]) - {"BTCUSDT", "ETHUSDT"} <= PC_COLLECTION)
        for k in ("test", "window", "single_use", "family", "members", "alpha_split"):
            self.assertEqual(r2[k], r1[k], k)
        self.assertEqual(r2["cost"]["profile"], "assumed_public_v1")
        self.assertEqual(r2["cost"]["criteria_sha256"], CRIT_V3_CONFIG_SHA256)
        self.assertEqual(list(r2["pending_at_freeze"]), ["Q2"])
        self.assertIn("Q1", r2["decided"])

    def test_iw1_refreeze_draft(self):
        mem = json.loads(IW1_REFREEZE.read_text())
        sha = hashlib.sha256(IW1_V2.read_bytes()).hexdigest()
        self.assertEqual(mem["prereg_sha256"], sha)
        # only a later change of the evaluation code may be reported (FROZEN_CODE_CHANGED)
        issues = [i for i in pg.check_members_file(mem, sha) if "FROZEN_CODE_CHANGED" not in i]
        self.assertEqual(issues, [])
        self.assertLessEqual(len(mem["members"]), 20)
        self.assertEqual(pg.evaluate(IW1_V2)["lifecycle"], "DRAFT")

    def test_old_freeze_still_flagged(self):
        sha = hashlib.sha256((DISC / "config" / "independent_window_v1.json").read_bytes()).hexdigest()
        old = json.loads(OLD_MEMBERS.read_text())
        self.assertTrue(any("refreeze" in i for i in pg.check_members_file(old, sha)))


if __name__ == "__main__":
    unittest.main()
