# AI Trade Desk

**검증 게이트형 국내주식 분석·자동매매 시스템** — 코스피·코스닥 시가총액 상위 종목을 매일 장 마감 후 수집하고, 과거 자료로 *통과한* 팩터만으로 후보를 고르고, 모의 계좌(기본) 또는 한국투자증권 계좌로 매매합니다. 결과는 GitHub Pages 대시보드와 텔레그램으로 받습니다.

> ⚠️ 투자 권유가 아닙니다. 과거 검증은 미래 수익을 보장하지 않으며, 실계좌 주문의 결과와 책임은 사용자에게 있습니다.

---

## 1. 무엇이 다른가

일반적인 "AI 주식 분석 시스템" 강의형 구조(수집 → 다요인 점수 → 신호 → 대시보드 → 텔레그램 → 스케줄 자동화)를 그대로 따르되, **점수에 들어가는 근거를 사람이 정하지 않고 데이터가 허락한 것만 쓰도록** 바꿨습니다.

| 항목 | 일반적인 구성 | AI Trade Desk |
|---|---|---|
| 팩터 가중치 | 사람이 정한 고정 배점(예: 15점 만점) | 검증 통과 팩터만 가중치 > 0, 나머지는 0 |
| 통과 기준 | 백테스트 수익률 그래프 | 탐색 70% / 확인 30% 분리, 다중검정(Bonferroni) 보정 t, 겹치지 않는 표본 |
| 과적합 방지 | — | 확인 구간 열람 횟수를 `state/confirm_log.json` 에 누적 기록해 대시보드에 공개 |
| 근거 없는 날 | 그래도 상위 N 종목 추천 | 통과 팩터가 없으면 **추천 0 종목** |
| 체결 가정 | 신호일 종가 | 다음 날 시가 진입, 왕복 비용 차감, 유동성 하위 20% 제외 |
| 모의매매 규칙 | 검증과 별개 | 검증과 **같은 체결 규칙**(갭 손절·손절 우선·시간청산) |
| LLM 역할 | 종목 판단 | 숫자를 사람 말로 풀어 주는 **설명 전용**, 판정 권한 없음 |
| 실전 주문 | 환경변수 하나 | 이중 잠금 (`DESK_MODE=live` + 확인 문구) |

---

## 2. 구조

```
desk/
  config.py      전역 설정 (환경변수로 덮어쓰기)
  data.py        네이버 시세·수급 수집 + 캐시, 오프라인 시험용 가상 시장
  factors.py     9개 팩터, 미래정보 없는 다음날 시가 기준 수익률
  validate.py    검증 게이트 (핵심)
  score.py       통과 팩터 가중 점수, ATR 손절·목표, 1% 리스크 사이징
  ai.py          (선택) LLM 해설 — Anthropic 또는 로컬 Ollama
  notify.py      텔레그램
  broker/
    paper.py     모의 장부
    kis.py       한국투자증권 Open API (모의투자/실전)
run_daily.py     장 마감 후: 수집→검증→후보→모의매매→대시보드 JSON→알림
run_trade.py     장중: KIS 계좌 매수·손절·목표·시간청산
docs/index.html  대시보드 (GitHub Pages)
.github/workflows/daily.yml   평일 16:20 KST 자동 실행 후 결과 커밋
tests/           검증 게이트·체결 규칙·실전 잠금 테스트
```

### 팩터 (모두 "클수록 좋다"는 가설 — 맞는지는 게이트가 판정)

| 키 | 정의 | 문헌 |
|---|---|---|
| `frgn_hold` | 외국인 보유율 | Choe, Kho & Stulz (1999) |
| `frgn_hold_chg` | 외국인 보유율 20일 변화 | 〃 |
| `flow_5d` | 외국인+기관 5일 순매수 / 20일 평균거래량 | — |
| `mom_12_1` | 65~5거래일 전 수익률 | Jegadeesh & Titman (1993) |
| `trend` | 20일선/60일선 − 1 | — |
| `pos_52w` | 52주 범위 내 위치 | George & Hwang (2004) |
| `low_vol` | −20일 변동성 | Ang et al. (2006), Frazzini & Pedersen (2014) |
| `vol_surge` | 거래량 / 20일 평균 | — |
| `vcp` | 52주 고점권에서 ATR(10)/ATR(50) 수축 | Minervini (2013) |

---

## 3. 빠른 시작

```bash
pip install -r requirements.txt

# 1) 인터넷 없이 가상 시장으로 전체 흐름 확인
DESK_DATA=synthetic DESK_SYN_EFFECT=0.0002 python run_daily.py --revalidate --no-notify

# 2) 실제 시세(네이버)로 실행 — 첫 실행은 종목당 과거 수집이라 10~20분
python run_daily.py --revalidate

# 3) 대시보드 보기
cd docs && python -m http.server 8000   # http://localhost:8000
```

Windows 작업 스케줄러 예시: 평일 16:20 `python run_daily.py`, 평일 08:50 `python run_trade.py` (KIS 사용 시).

---

## 4. GitHub 에서 자동 운영

1. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main` / `docs`
2. **Settings → Secrets and variables → Actions**
   - Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, (선택) `ANTHROPIC_API_KEY`
   - Variables: (선택) `DESK_TOP_N`, `DESK_AI=anthropic`
3. **Actions → daily-desk → Run workflow** 로 첫 실행 (`revalidate` 체크)

이후 평일 아침(서버 PC 업로드 직후, 늦어도 07:50 KST) 자동 실행되고 `docs/data/latest.json` 과 `state/` 가 커밋됩니다. 모의 장부도 저장소에 쌓이므로 기록이 남습니다.

### 서버 PC 수급 연동 (외국인·기관 수급 팩터용)

네이버 수급 페이지는 해외(GitHub 서버)에서 막히므로, 국내에 있는 서버 PC 의 `C:\toss-core\flow.db` 를 매일 올린다.

```powershell
git clone https://github.com/cncnature/ai-trade-desk C:\ai-trade-desk
cd C:\ai-trade-desk
pip install pandas
python pc\export_flow.py            # 첫 실행: 전체 이력 업로드 → Actions 자동 실행
powershell -ExecutionPolicy Bypass -File pc\install_task.ps1   # 평일 07:30 자동 업로드 등록
```

흐름(KST): 07:20 토스 코어 자동 운영이 flow.db 갱신 → 07:30 업로드(새 수급이 없어도 신호 파일은 올라감) → 즉시 분석·텔레그램.
GitHub 예약 실행은 몇 시간씩 밀릴 수 있어, 서버 PC가 06:30(배당)·16:05(종가)·20:15(저녁 평가)에도 `pc/kick.py` 로 실행 신호를 보낸다. 예약은 예비.
대시보드 '시스템 점검' 칸에서 단계별로 오늘 결과가 나왔는지, 어디가 막혔는지 본다.

> 증권사 키는 Actions 에 넣지 마세요. 실계좌 주문(`run_trade.py`)은 **내 PC에서만** 실행하도록 설계했습니다.

---

## 5. 실계좌로 가는 단계

| 단계 | 설정 | 주문 대상 |
|---|---|---|
| ① 모의 장부 | `DESK_MODE=paper` (기본) | 없음 — `state/paper_ledger.json` |
| ② KIS 모의투자 | `DESK_MODE=kis_mock` + KIS 모의 키 | KIS 모의계좌 |
| ③ 실전 | `DESK_MODE=live` + `DESK_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY` + 실전 키 | 실계좌 |

권장: ①의 실측 성적(대시보드 "모의 계좌")이 검증 게이트의 확인 구간 성적과 비슷하게 나오는지 최소 몇 주 지켜본 뒤 ②→③.

KIS 주문 TR(`TTTC0012U`/`TTTC0011U`)은 2024년 변경 공지 기준입니다. 운영 전 KIS 개발자센터 최신 문서로 확인하세요.

---

## 6. 검증 게이트 방법

1. 날짜마다 유동성 하위 20%를 뺀 종목 중 팩터 상위 20%를 바스켓으로 묶는다.
2. 바스켓의 *t+1 시가 → t+10 종가* 수익률(왕복 0.25% 차감)에서 같은 날 전체 평균을 뺀 값이 초과수익.
3. 보유기간이 겹치면 t 값이 부풀려지므로 10거래일 간격으로만 표본을 뽑는다.
4. 앞 70% **탐색 구간**: 평균 > 0, t ≥ Bonferroni 보정값(팩터 9개 → 약 2.77).
5. 뒤 30% **확인 구간**: 평균 > 0, t ≥ 1.5 → 통과. 가중치 = min(확인 t, 3).
6. 검증은 주 1회(월요일) 또는 `--revalidate` 때만 다시 돌린다. 매일 다시 보면 확인 구간이 사실상 탐색 구간이 되기 때문.

**가상 시장 시험 결과** (`tests/`): 외국인 보유율에 약한 초과수익을 심어 둔 시장에서는 해당 팩터가 통과하고, 효과를 0으로 둔 시장에서는 통과 팩터가 거의 나오지 않는지 자동 테스트로 확인합니다.

---

## 7. 모의 계좌 수동 조작 (대시보드 버튼)

보유 종목 표의 **[목표 %] [손절 %] [청산]**, 위쪽의 **[전체 목표 %] [전체 손절 %] [기본값 복원] [전체 청산]** 버튼은
GitHub 새 이슈 화면을 명령 제목으로 채워 연다. 저장소 주인이 **Submit** 하면 `manual` 워크플로가 1~2분 안에 장부를 고치고
이슈에 결과를 답한 뒤 닫는다. 다른 사람이 연 이슈는 실행되지 않는다.

| 이슈 제목 | 동작 |
|---|---|
| `CLOSE 005930` / `CLOSE ALL` | 지금 가격(장중 현재가·장후 종가)으로 청산 — 수익이면 수동 익절, 손실이면 수동 손절 |
| `TARGET 005930 3` | 목표가 = 매수가 +3% (이미 넘었으면 즉시 청산) |
| `STOP ALL 2` | 보유 전체와 앞으로 살 종목의 손절 = 매수가 −2% |
| `RESET ALL` | ATR 기본값(목표 ×3, 손절 ×2)으로 복원 |

목표·손절을 바꾸면 모의 성적은 검증된 규칙(10일 보유)과 달라진다.

## 8. 한계

- 네이버 페이지 구조가 바뀌면 수집이 깨집니다. 운영 단계에서는 KRX OpenAPI·KIS·토스 API로 `data.py` 소스를 교체하세요.
- 상장폐지 종목이 유니버스에 없으므로(생존 편향) 과거 성적은 다소 낙관적입니다.
- 시총 상위 종목만 다루므로 소형주 효과는 보지 않습니다.
- 지수 대비 성적(`vs_index`)이 마이너스면, 통과했더라도 지수 ETF 보유보다 못했다는 뜻입니다. 대시보드에서 함께 확인하세요.

---

## References

- Jegadeesh, N., & Titman, S. (1993). Returns to buying winners and selling losers. *Journal of Finance*, 48(1), 65–91.
- George, T. J., & Hwang, C.-Y. (2004). The 52-week high and momentum investing. *Journal of Finance*, 59(5), 2145–2176.
- Ang, A., Hodrick, R. J., Xing, Y., & Zhang, X. (2006). The cross-section of volatility and expected returns. *Journal of Finance*, 61(1), 259–299.
- Frazzini, A., & Pedersen, L. H. (2014). Betting against beta. *Journal of Financial Economics*, 111(1), 1–25.
- Choe, H., Kho, B.-C., & Stulz, R. M. (1999). Do foreign investors destabilize stock markets? The Korean experience in 1997. *Journal of Financial Economics*, 54(2), 227–264.
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). …and the cross-section of expected returns. *Review of Financial Studies*, 29(1), 5–68.
- Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. (2014). Pseudo-mathematics and financial charlatanism. *Notices of the AMS*, 61(5), 458–471.
- White, H. (2000). A reality check for data snooping. *Econometrica*, 68(5), 1097–1126.
- Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. *Journal of Finance*, 72(4), 1611–1644.
- Minervini, M. (2013). *Trade Like a Stock Market Wizard*. McGraw-Hill.

## License

MIT
