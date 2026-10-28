# v3 e-값 누적 검증 최종형 초안 (ADR 0042, 2026-10-28)

효력 없음. 사전등록을 활성화하지 않았고 검증·잠금·IW1·종이 구간을 열지 않았다. 리허설은 DEV 구간(2021~2023)만 읽었다.

## 동동 님 결정 (2026-10-28, "추천으로 진행")
1. v2는 등록하지 않는다(1/33 < 5). v2 초안은 DRAFT로 그대로다.
2. v3는 2026-12-01 첫 블록으로 마무리한다(베팅 규칙 WS-R 식 (15)/(26) 베팅곱).
3. v3 알파는 0.0091 그대로 두고, v2 몫 0.0213은 옮기지 않는다.

## 파일
| 파일 | 내용 |
|---|---|
| `walkforward_evalue_v3_FINALFORM_DRAFT.json` | 최종형 초안. 게이트 판정은 DRAFT이고 빠진 필수 항목은 없다 |
| `rehearsal_members_v3_DRAFT.json` | 리허설 구성원: R9 후보 33개 중 처음 5개, DEV 배율 포함. **실제 구성원이 아니다** |
| `rehearsal_block_2023-06.json`, `rehearsal_block_2022-06.json` | DEV 달 두 개로 돌린 블록 계산(장부에는 기록하지 않음) |

## 리허설 결과 (측정 비용 PE-ed757f…, 9종목)
- DEV 배율: S1(2021-04-01~2023-09-18, 901일) 묶음 일별 표준편차 42.1bp → k = 2.38(상한 10 안). 이 묶음의 S1 연 샤프는 0.02다. 진입 기준이 없으니 약한 후보도 그대로 들어간다는 뜻이다.
- 2023-06: 133건 거래, 16일째 하루 끝에서 −5.76%로 손절선(−5.48%)에 걸려 X = −5.76%.
- 2022-06: 59건 거래, 손절 없이 X = −3.03%.
- 두 달 다 손실이다. 리허설은 도구가 실제 자료에서 끝까지 도는지 확인하려고 돌렸고, 성과를 판단하려는 것이 아니다.
- 실제 블록 기록은 게이트가 ACTIVE가 아니면 거부된다(확인: `GateRefused: preregistration is DRAFT`).

## 활성화까지 남은 일 (동동 님 최종 확인 뒤, 2026-12-01 00:00Z 전에 커밋)
1. 초안과 이 README를 마지막으로 읽기
2. 동결할 코드로 DEV 전용 evolve 상태 디렉터리 만들기(`runner.py --mode evolve --dev-only`)
3. 이 초안으로 활성화 파일 쓰기(상태 줄 교체, `pending_at_activation` 삭제)
4. `python3 run/evalue_v3_block.py freeze --prereg <활성화 파일> --state-dir <dir> --dev-data-dir <data> --out <members>`, 이어서 `dev-scale`
5. 동동 님이 승인한 `prereg_activation_v1` 기록 커밋
6. `prereg_gate.py --prereg … --members … --activation …`가 ACTIVE를 내는지 확인

늦으면 첫 블록이 2027-01이 된다.

## 매달 할 일 (활성화 뒤)
첫 블록이 끝나고 바이낸스 월별 파일이 올라오면 `run/evalue_v3_block.py block --block 2026-12 --prereg … --members … --activation … --data-dir … --ledger run/audit/evalue_v3/ledger_v3.jsonl`. 블록당 한 번, 순서대로만 기록되고, E ≥ 약 110이면 기각한다.
