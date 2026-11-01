#!/usr/bin/env python3
"""ADR 0055: re-aggregate already-published DEV results (no market data read) and
simulate pipeline pass probabilities under strict vs relaxed criteria.
Criteria definitions are the ADR 0040 set: a S1>chance_max, b alpha t>=2, c both halves>0,
d S2>0.5, e cost x2 S1>0. Unknown field = treated as PASS (upper bound for survivors)."""
import csv, json, sys, numpy as np
D1, D2 = 931, 427          # S1 / S2 days
ANN = 365.0
rows = list(csv.DictReader(open(sys.argv[1])))
def f(x): return None if x in ("", None) else float(x)
SCEN = {
 "S0_strict(a,b,c,d,e)": "abcde",
 "S1_drop_a": "bcde",
 "S2_drop_a_b": "cde",
 "S3_halves_only(c)": "c",
 "S4_sign_consistent(S1>0,S2>0,c)": "sc",
}
def crit(r, key):
    s1,s2,h1,h2,t,x2,ch = (f(r[k]) for k in ("s1_net","s2","half1","half2","alpha_t","x2_pos","chance_max"))
    if s1 is None and h1 is not None: s1 = (h1+h2)/2
    if key=="a": return ch is None or s1 is None or s1>ch
    if key=="b": return t is None or t>=2
    if key=="c": return h1 is None or (h1>0 and h2>0)
    if key=="d": return s2 is None or s2>0.5
    if key=="e": return x2 is None or x2>0 if r["x2_pos"] else True
    if key=="s": return s1 is None or s2 is None or (s1>0 and s2>0)
out = {"n_rows": len(rows), "survivors": {}}
for name, keys in SCEN.items():
    surv = [r["id"] for r in rows if all(crit(r,k) for k in keys)]
    out["survivors"][name] = surv
# fully evaluable rows (all of s1,s2,half1,half2 present)
full = [r for r in rows if all(f(r[k]) is not None for k in ("s2","half1","half2"))]
out["n_full_rows"] = len(full)
out["survivors_full_rows_only"] = {n:[r["id"] for r in full if all(crit(r,k) for k in ks)] for n,ks in SCEN.items()}
# simulation
rng = np.random.default_rng(20261101)
N = 40000
def stats(sr, n=N):
    mu = sr/ANN; 
    r1 = rng.normal(mu,1/np.sqrt(ANN),(n,D1)); r2 = rng.normal(mu,1/np.sqrt(ANN),(n,D2))
    sh = lambda x: x.mean(1)/x.std(1)*np.sqrt(ANN)
    h = D1//2
    return sh(r1), sh(r2), sh(r1[:,:h]), sh(r1[:,h:])
chance = {"a075":0.75,"a1_11":1.11,"a244":2.44}
tab = []
for sr in [0,0.25,0.5,0.75,1.0,1.5,2.0,3.0]:
    s1,s2,h1,h2 = stats(sr)
    t = s1*np.sqrt(D1/ANN)
    A = s1>0.75; B = t>=2; C=(h1>0)&(h2>0); Dd = s2>0.5
    row = {"true_SR":sr,
      "P_a(S1>0.75)":A.mean(),"P_b(t>=2)":B.mean(),"P_c(halves>0)":C.mean(),"P_d(S2>0.5)":Dd.mean(),
      "P_a_and_b_and_c_and_d":(A&B&C&Dd).mean(),
      "P_S2drop_a_b":(C&Dd).mean(),
      "P_S4_sign_consistent":((s1>0)&(s2>0)&C).mean(),
      "P_entry_S1>=1.0":(s1>=1.0).mean(),
      "P_chance244_S1>2.44":(s1>2.44).mean()}
    tab.append({k:(round(float(v),4) if k!="true_SR" else v) for k,v in row.items()})
out["power_table"] = tab
# expected survivors among n null rows
nul = [x for x in tab if x["true_SR"]==0][0]
n = len(rows)
pos = {}
s1,s2,h1,h2 = stats(0.0)
cond = s1>0   # rows were collected because S1 net was > 0
t0 = s1*np.sqrt(D1/ANN)
C0=(h1>0)&(h2>0)
pc = lambda m: float((m&cond).sum()/cond.sum())
nf = len(full)
out["expected_null_survivors_full_rows_given_S1pos"] = {
 "n_full_rows": nf,
 "S0_strict": round(nf*pc((s1>0.75)&(t0>=2)&C0&(s2>0.5)),2),
 "S2_drop_a_b": round(nf*pc(C0&(s2>0.5)),2),
 "S4_sign_consistent": round(nf*pc((s2>0)&C0),2),
 "observed": {n: len(v) for n,v in out["survivors_full_rows_only"].items()},
 "note": "true net SR=0 for every row; rows treated independent (they are not)"}
out["expected_null_survivors"] = {
 "S0": round(n*nul["P_a_and_b_and_c_and_d"],3),
 "S2_drop_a_b": round(n*nul["P_S2drop_a_b"],2),
 "S4_sign_consistent": round(n*nul["P_S4_sign_consistent"],2),
 "note": "rows treated independent (they are not); true net SR=0"}
# years of data needed (one-sided alpha .05 / .05/K, 80% power): T=((z_a+z_b)/SR)^2
from math import erf
def zinv(p):  # normal quantile via bisection
    lo,hi=-10,10
    for _ in range(80):
        m=(lo+hi)/2; c=0.5*(1+erf(m/2**0.5))
        lo,hi=(m,hi) if c<p else (lo,m)
    return (lo+hi)/2
need={}
for K in (1,10,100):
    za=zinv(1-0.05/K); zb=zinv(0.8)
    need[f"K={K}"]={str(sr):round(((za+zb)/sr)**2,1) for sr in (0.5,1.0,1.5,2.0,3.0)}
out["years_needed_80pct_power"]=need
json.dump(out, open(sys.argv[2],"w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
