# 신규 상장 자료 수집기 사용법 (Oracle VM 또는 PC)

클라우드 연구 세션은 바이낸스 선물(fapi)에 접속할 수 없고, 수집기는 클라우드에서 돌리지 않는다. VM(도쿄)이나 PC에서 돌린다. 표준 라이브러리만 쓰고, 키·계정·주문 코드는 없다.

## 파일 (3개를 같은 폴더에)
`newlisting_collect.py`, `data_fetch.py`, `portable_util.py` (저장소 `run/discovery/`, 프로젝트 파일 `collector/newlisting/`에도 복사).

## 실행
```
python3 newlisting_collect.py --allow-network --hours 720 --out ~/newlisting_data
```
- 첫 실행은 그 시점의 모든 USDT 무기한 종목을 "기존 종목"(baseline)으로 저장하고 추적하지 않는다. 이후 새로 나타나는 종목만 신규 상장으로 기록한다.
- 신규 상장은 상장일(onboardDate)부터 30일 추적: 첫 24시간 30초, 3일까지 5분, 30일까지 15분 간격으로 호가 100단 중 50단·마크가·펀딩을 저장하고, 1분봉과 모든 체결(aggTrades)을 이어 받는다.
- 최대 720시간(30일)이면 정상 종료한다. 재실행하면 `state.json`으로 이어진다. 끝나면 `listing_manifest.json`에 SHA-256을 봉인한다(`--manifest-only`는 네트워크 없이 다시 봉인).
- 이미 올라온 지 며칠 안 된 종목을 따로 보고 싶으면 첫 실행에만 `--track-recent-days 5`. 이 종목들은 `late_start`로 표시되며 점 시점 이벤트가 아니므로 사전등록 이벤트에서 제외한다.

## VM 상시 서비스 (예: orderbook 서비스와 같은 방식)
`Restart=always`, `MemoryMax=200M`, `Nice=10`, 중지 시 `--manifest-only`. 디스크는 신규 1종목 30일에 수백 MB 정도(첫 24시간 체결이 많은 종목이 대부분). 디스크 여유 500MB 미만이면 기록을 멈춘다. 먼저 `--hours 0.05`로 짧게 돌려 `listing_events.jsonl`과 `state.json`이 생기는지만 확인한다.

## 올리는 법
수집이 쌓이면 `~/newlisting_data`를 zip으로 묶어 이 프로젝트 채팅에 첨부한다(비공개 저장소라 VM에서 직접 받을 수 없음). `listing_manifest.json`을 같이 넣는다.

## 한계
- 상장 직후 첫 몇 분은 감지 지연(최대 `--discover-sec` 60초 + 첫 틱)만큼 비고, `delay_after_onboard_ms`로 확인된다.
- 시장 자료일 뿐 체결 기록이 아니므로 비용을 VERIFIED로 만들지 못한다.
- 사전등록 초안(`prereg_newlisting_short_forward_v2_DRAFT.json`)은 활성화되지 않았다.
