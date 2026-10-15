#!/usr/bin/env python3
# 보조 통계(서술용, 판정 규칙 없음): 펀딩 z와 선행수익의 선형 상관 + 정산 시점 자체 드리프트
import glob, numpy as np, pandas as pd
from scipy import stats
BASE="/home/z/my-project/NEW2_run/data"
def ms(s): return int(pd.Timestamp(s,tz="UTC").timestamp()*1000)
SPANS={"BTCUSDT":[("2021-05-02","2022-08-17"),("2023-04-07","2024-03-18")],
       "ETHUSDT":[("2021-05-02","2022-08-17"),("2024-12-10","2026-04-20")]}
cols=["open_time","open","high","low","close","volume","close_time","qv","n","tbv","tbqv","ig"]
for sym in SPANS:
    files=sorted(glob.glob(f"{BASE}/klines/{sym}-15m-*.csv"))
    k=pd.concat([pd.read_csv(f,header=None,names=cols) for f in files],ignore_index=True)
    k=k[pd.to_numeric(k["open_time"],errors="coerce").notna()]
    for c in ["open_time","open"]: k[c]=pd.to_numeric(k[c])
    k=k.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    ot=k["open_time"].to_numpy(dtype=np.int64); op=k["open"].to_numpy(dtype=float)
    f=pd.concat([pd.read_csv(x) for x in sorted(glob.glob(f"{BASE}/funding/{sym}-fundingRate-*.csv"))],ignore_index=True)
    f=f.drop_duplicates("calc_time").sort_values("calc_time").reset_index(drop=True)
    r=f["last_funding_rate"].to_numpy(dtype=float); t=f["calc_time"].to_numpy(dtype=np.int64)
    z=np.full(len(r),np.nan)
    for i in range(90,len(r)):
        w=r[i-90:i]; s=w.std(ddof=1)
        if s>0: z[i]=(r[i]-w.mean())/s
    for si,(a,b) in enumerate(SPANS[sym]):
        sm,em=ms(a),ms(b)
        res=[]
        for H in (16,48):
            zs,fw=[],[]
            for j in range(len(t)):
                if not (sm<=t[j]<em) or not np.isfinite(z[j]): continue
                ei=np.searchsorted(ot,t[j],side="right"); xi=ei+H
                if xi>=len(ot) or ot[xi]>=em: continue
                if (np.diff(ot[ei:xi+1])!=900000).any(): continue
                fw.append((op[xi]-op[ei])/op[ei]); zs.append(z[j])
            if len(zs)>20:
                rho,p=stats.pearsonr(zs,fw)
                res.append(f"h{H}: r={rho:+.4f} p={p:.3f} n={len(zs)}(유효n={len(zs)//H})")
        # 정산 시점 자체 드리프트(조건 없음): 정산 직후 4봉 수익 평균
        dr=[]
        for j in range(len(t)):
            if not (sm<=t[j]<em): continue
            ei=np.searchsorted(ot,t[j],side="right"); xi=ei+4
            if xi>=len(ot) or ot[xi]>=em: continue
            if (np.diff(ot[ei:xi+1])!=900000).any(): continue
            dr.append((op[xi]-op[ei])/op[ei])
        res.append(f"정산후4봉평균: {np.mean(dr)*1e4:+.2f}bp (n={len(dr)})")
        print(f"{sym} span{si+1} {a}~{b}: " + " | ".join(res))
