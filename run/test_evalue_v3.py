"""evalue_v3.e_path (stdlib, ADR 0042) equals the simulated bet rule
evalue_walkforward_sim.log_e_hedged_prpl_bet (ADR 0025/0030/0032)."""
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "discovery"))
import evalue_v3 as V  # noqa: E402
import evalue_walkforward_sim as S  # noqa: E402

FINAL = HERE / "audit" / "evidence_v3_final_20261028" / "walkforward_evalue_v3_FINALFORM_DRAFT.json"


class EPathEqualsSim(unittest.TestCase):
    def test_equal(self):
        p = V.params(V.load_prereg(FINAL)[0])
        s0 = p["block_sd"] / (p["B"] + p["c"])
        Z = np.maximum(np.random.default_rng(7).normal(0.02, 0.5, size=(3, 40)), -1.0)
        ref = S.log_e_hedged_prpl_bet(Z, s0, alpha=p["alpha"], lam_max=p["lam_max"])
        for i in range(3):
            got = [r["log_e"] for r in V.e_path(list(Z[i]), s0=s0, alpha=p["alpha"], lam_max=p["lam_max"])]
            np.testing.assert_allclose(got, ref[i], rtol=1e-12, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
