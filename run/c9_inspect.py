#!/usr/bin/env python3
# C9 결과 상세 검토 — 구성 일관성 및 탐색 관측 등록 자격(X1-R 엄격 기준: 유효n>=22 + 엔진 net>0 + 구성 일관)
import json

d = json.load(open('/home/z/my-project/NEW2_run/results/c9_crosscheck_results.json'))
d2 = json.load(open('/home/z/my-project/NEW2_run/results/c9_dvol_results.json'))

def f(v, nd=2):
    return 'n/a' if v is None else f'{v:.{nd}f}'

print('=== L90 B90 h672 (구성 일관 후보) 엔진 상세 ===')
for c in d['cells']:
    if c['L'] == 90 and c['bucket'] == 'B90' and c['horizon'] == 672:
        print(f"{c['symbol']} {c['span'][:22]}: n={c['n']} 유효n={c['n']/672:.1f} "
              f"총bp={f(c.get('total_bp'),1)} 정액순16={f(c.get('net_flat16'),1)} "
              f"엔진10k={f(c.get('net_o10k'),1)} 비용={f(c.get('cost_o10k_bp'),1)} holm10k={f(c.get('p_holm_o10k'),3)}")
print()
print('=== 유효n>=22 & 엔진($10k) net>0 셀 전수 ===')
cnt = 0
for c in d['cells']:
    if c.get('net_o10k') is not None and c['n'] / c['horizon'] >= 22 and c['net_o10k'] > 0:
        cnt += 1
        print(f"{c['symbol']} {c['span'][:22]} L{c['L']} {c['bucket']} h{c['horizon']}: "
              f"n={c['n']} 유효n={c['n']/c['horizon']:.1f} 엔진10k={c['net_o10k']:.2f} holm={c['p_holm_o10k']:.3f}")
print(f'→ 해당 셀 {cnt}개')
print()
print('=== 진단(정액) L90/B90/h672 대조 ===')
for c in d2['report']['cells']:
    if c['L'] == 90 and c['bucket'] == 'B90' and c['horizon'] == 672:
        print(f"{c['symbol']} {c['span'][:22]}: n={c['n']} 유효n={c['valid_n']:.1f} 총bp={f(c['total_bp'],1)} "
              f"순16={f(c['net16'],1)} p={c['p']:.3f} 반대순16={f(c['oppose_net16'],1)} p_ctrl={f(c['p_ctrl'],3)}")
print()
print('=== 진단(정액) L90/B80/h672 대조 ===')
for c in d2['report']['cells']:
    if c['L'] == 90 and c['bucket'] == 'B80' and c['horizon'] == 672:
        print(f"{c['symbol']} {c['span'][:22]}: n={c['n']} 유효n={c['valid_n']:.1f} 총bp={f(c['total_bp'],1)} "
              f"순16={f(c['net16'],1)} p={c['p']:.3f}")
