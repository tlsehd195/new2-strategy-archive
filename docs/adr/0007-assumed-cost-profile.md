# ADR 0007 — 가정 비용(ASSUMED)과 공개 시세 추정(ESTIMATED_FROM_PUBLIC_DATA): 실측 체결 없이 연구를 계속하되 승격은 막는다

- 상태: Accepted (PR #6·#7 병합, main `c39578f`). 추정기 기준은 [ADR 0008](0008-residual-audit-cost-estimate-lookahead-forward-integrity.md)의 v2로 대체됨
- 날짜: 2026-10-16
- 범위: `run/discovery/costs_bridge.py`, `config/cost_assumed_public_v1.json`(신규), `public_market_collect.py`·`public_cost_estimate.py`·`config/cost_public_estimate_v1.json`·`tests/test_public_estimate.py`·`docs/공개시세_비용추정_로컬실행_안내.md`(신규), `pipeline.py`, `runner.py`, `autoloop.py`, `ops.py`, `run_cycle.sh`(주석), `tests/test_assumed_cost.py`(신규), `.github/workflows/research-ci.yml`
- 선행: [ADR 0006](0006-cost-verification-forward-paper-data-manifest-ops-ci.md)

## 맥락
ADR 0006에 따라 비용 모델은 계정 체결 보고서로만 검증할 수 있다. 그런데 사용자는 미성년자라 거래소 계정을 만들 수 없고, 따라서 체결 기록도 없다. 다른 사람 계정을 빌리는 방법은 택하지 않는다. 사용자는 공개 수수료표에 보수적인 슬리피지 여유를 더한 가정 비용으로 연구를 계속하는 방향을 승인했다(2026-10-16). 이때 검증 게이트와 통과 기준은 완화하지 않는다.

## 결정
1. **프로필 파일** `config/cost_assumed_public_v1.json` (`cost_assumed_profile_v1`). 실데이터로 이 프로필을 돌리기 **전에** 값을 확정했다.
   - 수수료: 5bp. 바이낸스 USDT-M 공개 수수료표의 기본 등급(BNB 할인 없음) taker 0.05%이고, 모든 체결을 taker로 가정한다. 이 환경에서는 바이낸스 접속이 막혀 있어 페이지를 직접 받지 못했다. 그래서 파일에 "공식 페이지와 재확인 필요"라고 적었다.
   - 반스프레드 2bp에 슬리피지 여유 3bp를 더해, 한쪽당 5bp로 둔다. 데이터로 추정한 값이 아니고, 지연·대기열·역선택을 넉넉히 덮도록 정한 값이다.
   - 시장 충격 계수 0.1과 참여 상한 5%는 NEW2 기본값 그대로다.
   - 결과적으로 한쪽당 비용은 기본 프로필(7bp)보다 3bp 높은 약 10bp(충격 제외)다.
2. **상태 `ASSUMED_COST_MODEL`**: `CostContext.status`는 다음 셋 중 하나다.
   - `VERIFIED`: 봉인된 검증 기록이 있을 때만.
   - `ASSUMED_COST_MODEL`: 이 프로필을 쓸 때.
   - `UNVERIFIED_COST_MODEL`: 기본값.
   - 가정 프로필은 `verified=True`가 될 수 없다. 생성 단계에서 `CostConfigError`가 난다.
   - 프로필 파일에 `verified`나 `verification` 필드가 있으면 거부한다. `status`가 다르거나, 값이 음수이거나 NaN이어도 거부한다.
3. **표시**:
   - 평가 기록의 `cost_model`에 `status`, 프로필 ID·SHA-256, `"비용 미검증(ASSUMED)"` 문구가 들어간다.
   - 승격 심사에는 `UNVERIFIED_COST_MODEL`과 `ASSUMED_COST_MODEL` 라벨이 둘 다 붙고, 결정은 `BLOCKED_COST`다.
   - 사이클 요약에는 `cost_status`가 들어간다.
4. **승격 차단 유지**:
   - `verified_for`가 `ASSUMED_COST_MODEL`을 사유로 거부하므로 `PAPER_TEST_READY`가 될 수 없다.
   - 전진 페이퍼 등록도 같은 검사를 거쳐 거부된다.
   - 실제 체결 보고서가 생기면 기존 `cost_calibration` 경로로 검증을 닫는다. 그 기록으로 만든 컨텍스트에서만 승격이 가능하다.
5. **선택 방법**:
   - 환경 변수 `NEW2_COST_PROFILE`의 기본값은 `new2_default`이고 동작은 이전과 같다. `assumed_public_v1`을 주면 이 프로필을 쓴다.
   - 모르는 이름을 주면 잠금을 잡기 전에 `CONFIG_ERROR`를 내고 종료 코드 1로 끝난다.

6. **공개 시세 추정** (사용자 결정 2026-10-16: "공개 시세 api로 할게").
   - `public_market_collect.py`는 키가 필요 없는 공개 엔드포인트만 호출한다(`/fapi/v1/depth`, `klines`, `aggTrades`, `time`). 허용 목록 밖의 경로나 서명·타임스탬프·API 키 파라미터는 코드가 거부한다. 네트워크 사용은 `--allow-network`로 명시해야 하고, 수집 시간은 `--hours`로 반드시 제한된다. 요청 간격은 5초 이상이어야 한다.
   - `public_cost_estimate.py`는 네트워크를 쓰지 않는다. 스냅샷마다 호가를 걸어 1천~100만 USDT 주문의 한쪽 비용을 잰다. 시장 충격 계수는 `초과비용 = k·√(주문/직전 15분 거래대금)`으로 적합한다.
   - 보수적 선택은 모두 데이터를 보기 전에 `config/cost_public_estimate_v1.json`에 고정했다. 반스프레드는 90% 분위수에 한 수집 간격 동안의 중간가 변동 중앙값(지연 여유)을 더한다. 충격 계수는 최소제곱 추정과 90% 분위수 비율 중 큰 값을 쓴다. 종목 간에는 최댓값을 쓴다.
   - 사용 조건은 종목별 72시간 이상, 스냅샷 2,000개 이상, 공백 2% 이하, 무효 1% 이하다. 조건을 못 채우면 `INSUFFICIENT_PUBLIC_DATA`이고 엔진이 거부한다.
   - 수수료는 공개 데이터에 없으므로 ASSUMED 프로필의 공개 수수료표 값(taker 5bp)을 그대로 쓰고, `observed=false`로 기록한다.
   - 엔진에서는 `NEW2_COST_PROFILE=public_estimate:<record.json>`으로 쓴다. 이때 상태는 `ESTIMATED_FROM_PUBLIC_DATA`이고 `verified=False`다. 승격 심사 라벨과 `BLOCKED_COST` 처리는 ASSUMED와 같다. 기록 해시가 맞지 않으면 거부한다.
   - 이 컨테이너는 바이낸스 접속이 막혀 있으므로 실행은 사용자 PC에서 한다. 절차는 `docs/공개시세_비용추정_로컬실행_안내.md`에 있다. 사용자에게 우회 접속을 안내하지 않는다.

## 설정·기준 변경 기록
| 항목 | 이전 | 새 값 | 컨텍스트 영향 |
|---|---|---|---|
| 비용 프로필 | 기본(UNVERIFIED)만 존재 | `assumed_public_v1` 추가 | 프로필 ID·파일 해시가 비용 identity에 들어가므로 별도 컨텍스트가 된다. 파일을 고쳐도 새 컨텍스트가 된다. |
| 기본 프로필 비용 identity | — | 형태 변경 없음 | 다만 `costs_bridge.py`, `pipeline.py`, `runner.py` 코드 해시가 바뀌므로 기본 프로필로 돌려도 새 컨텍스트가 된다(이전 PASS는 재사용하지 않는다). |
| 공개 시세 추정 기준 | 없음 | `cost_public_estimate_v1.json` 신규(데이터 수집 전에 고정) | 추정 기록 ID가 비용 identity에 들어가 별도 컨텍스트가 된다 |
| 승격·검증 기준 (`promotion_v2.json`, `cost_verification_v1.json`) | — | 변경 없음 | 없음 |

## 결과
- 실데이터 결과는 `run/audit/AUDIT_COMPLETION_20261016.md`의 부록(가정 비용)에 있다.
- 남은 위험이 있다. 가정 비용이 실제보다 낮으면 가짜 우위가 연구 후보로 남을 수 있고, 높으면 진짜 우위를 놓칠 수 있다. 어느 쪽이든 결과는 연구 후보일 뿐이고 승격되지 않는다.
- 공개 수수료표 값은 이 환경에서 재확인하지 못했다.

## 추가 기록 (2026-10-16, PR #7): Windows에서 실행되도록 분리
- 문제: 사용자가 Windows PC에서 `public_market_collect.py`를 실행하자 `engine_lib`이 불러오는 `fcntl`(유닉스 전용) 때문에 `ModuleNotFoundError`가 났다. 추정 스크립트도 `costs_bridge`를 통해 NEW2 소스 체크아웃이 필요했다.
- 결정: 사용자 PC에서 도는 두 스크립트는 `engine_lib`·`costs_bridge`·`data_quality`를 가져오지 않는다. 필요한 몇 가지 함수는 표준 라이브러리만 쓰는 `portable_util.py`에 두고, 수수료 가정은 프로필 파일을 직접 읽는다(엔진은 기록을 읽을 때 매개변수를 다시 검증한다).
- 크래시로 마지막 줄이 잘렸을 때는 새 줄에서 이어 쓰고, 추정기는 잘린 줄을 조용히 건너뛰지 않고 무효 스냅샷으로 센다.
- 테스트: `fcntl`과 NEW2 소스를 막은 하위 프로세스에서 두 스크립트를 불러오고 추정까지 실행한다.
- 평가·승격 기준과 비용 모델 값은 바뀌지 않았다. `portable_util.py`가 코드 신원에 들어가므로 컨텍스트 ID는 다시 바뀐다.
