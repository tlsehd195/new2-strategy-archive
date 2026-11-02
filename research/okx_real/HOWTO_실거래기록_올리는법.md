# 실거래 체결 기록 올리는 법 (OKX 앱)

목적: 비용 추정(ADR 0011·0049, 왕복 약 15~27bp)을 본인 계정의 실제 체결로 VERIFIED로 바꾸기. 모의투자(`okx_paper/`)는 슬리피지가 없어 비용 근거로 못 쓴다. 실거래는 `research/okx_real/real_fills.csv`에만 쌓는다.

## 가장 쉬운 방법 (스크린샷)
1. 거래 한 건이 끝나면 **포지션 상세 화면**(종목·방향·진입가·청산가·실현손익·최대보유·시각이 보이는 화면)을 캡처한다. 이번에 올려 주신 것과 같은 화면이다.
2. 같은 건의 **Linked orders**(연결 주문)를 눌러 나오는 체결 목록도 캡처한다. 분할 체결·메이커/테이커·수수료가 여기 나온다.
3. 가능하면 **Trading history(체결 내역)** 화면도 캡처한다: 체결 한 줄마다 가격·수량·수수료·Maker/Taker가 있다. 이것이 가장 좋은 자료다.
4. **펀딩비**(Funding fee, 계정 → 자산/명세서의 펀딩 내역)를 그 보유 기간만큼 캡처한다.

## 더 좋은 방법 (파일)
OKX 앱/웹에서 거래 내역(Trade history, Funding fee history)을 CSV로 내보내 올리면 한 번에 수십~수백 건을 처리할 수 있다. 캡처보다 이쪽을 권한다.

## 적을 항목 (CSV 열)
`no, venue, symbol, side, leverage, margin_mode, open_time_app, close_time_app, entry, exit, notional_usdt, realized_pnl_usdt, fee_usdt, funding_usdt, entry_liquidity, exit_liquidity, source, note`
- `notional_usdt`: 진입 때의 포지션 크기(Max held). 수수료·펀딩을 따로 모르면 `fee_usdt`, `funding_usdt`는 비워 둔다. 그러면 총손익−실현손익으로 합계만 역산한다.
- `entry_liquidity`, `exit_liquidity`: maker(지정가가 호가에 걸려 체결)·taker(시장가로 먹음)·mixed.
- 시각은 앱에 보이는 시각 그대로 적고 시간대(KST/UTC)를 알려 달라.
- 빈 틀은 `real_fills_TEMPLATE.csv`.

## 필요한 양
- 종목별 **200건 이상**이 있어야 비용을 VERIFIED로 올릴 수 있다(ADR 0049). 건수가 적으면 "추정치와 같은 방향인가" 정도만 본다.
- 소액이어도 된다. 크기별 슬리피지는 작은 주문이 시장가에 체결되는 비용(스프레드+수수료)이므로 오히려 깨끗하다. 단, 포지션 크기를 매 건 적어 달라(크기에 따라 비용이 다르다).
- 한 건을 여러 번 나눠 사고팔았으면 가능한 한 체결 단위로 올린다.
- 같은 방식(지정가/시장가)끼리 모아야 의미가 있다. 방식이 섞이면 `mixed`로 적는다.

## 실행
`python run/okx_real_cost.py` → 건별 가격 변동, 총손익, 실현손익, 역산 비용(bp), 종목별 평균과 부족 건수를 출력한다.
