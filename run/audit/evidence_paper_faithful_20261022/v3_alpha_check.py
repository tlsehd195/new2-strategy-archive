# Reproduce: cd run && python3 audit/evidence_paper_faithful_20261022/v3_alpha_check.py
# v3 alpha after the 7:3 split (0.0091): plug-in vs WS-R GROW vs WS-R PrPl-EB (alpha in the bet = 0.0091). Synthetic only.
import json, math, sys
import numpy as np
sys.path.insert(0, '.')
import evalue_walkforward_sim as ev
A3 = 0.0091
out = {"alpha_v3": A3, "dgp": "garch_t4", "n_paths": 5000, "blocks": 60, "seed": [20261022, 99], "rows": []}
rng = np.random.default_rng([20261022, 99])
L, B = ev.L_MULT * ev.BLOCK_SD, ev.B_MULT * ev.BLOCK_SD
c = ev.GAP_C * ev.BLOCK_SD
thr = math.log(1 / A3)
for sr in (0.0, 1.0, 2.0, 3.0, 4.0):
    X, _, _ = ev.direct_blocks(rng, "garch_t4", 5000 if sr else 20000, 60, sr, L, B)
    Z, sz0 = (X - c) / (B + c), ev.BLOCK_SD / (B + c)
    row = {"true_sr_ann": sr}
    for name, le in (("plugin", ev.log_e_plugin(Z, sz0)), ("wsr_grow", ev.log_e_wsr(Z, sz0)),
                     ("wsr_prpl_eb", ev.log_e_prpl_eb(Z, sz0, alpha=A3))):
        tau = ev.first_cross(le, thr)
        row[name] = {f"reject_by_{h}": float(np.mean(tau <= h)) for h in (12, 24, 36, 60)}
    out["rows"].append(row)
    print(sr, {k: (v["reject_by_24"], v["reject_by_36"], v["reject_by_60"]) for k, v in row.items() if k != "true_sr_ann"}, flush=True)
json.dump(out, open('audit/evidence_paper_faithful_20261022/evalue_v3_alpha_results.json', 'w'), indent=1)
