"""오늘 증시 날씨 — 간밤 미국 시장으로 오늘 코스피 시가 갭을 예상하고, 그런 날 시가에 사면 어땠는지 과거로 보여 준다.

사전 등록 (2026-11-12, 결과를 본 뒤 바꾸지 않음)
  예측 변수 하나: EWY(미국 상장 한국 ETF)의 간밤 수익률 — 한국 장 마감 뒤 미국에서 거래된 '한국 주식 값'
  모형: 코스피 갭(시가/전일 종가 − 1) = a + b × EWY 수익률, 마지막 250거래일을 뺀 앞부분으로만 맞춤
  확인: 마지막 250거래일(모형에 안 쓴 구간)에서 상관계수·방향 적중률·평균 오차를 그대로 보고
  날씨: 예상 갭 ≥ +1% ☀️ 맑음 · +0.3~1% 🌤 구름 조금 · ±0.3% ☁️ 흐림 · −1~−0.3% 🌦 비 조금 · ≤ −1% 🌧 비
        VIX ≥ 30 이면 ⚡ 변동성 경보를 덧붙임
  매매 판단 근거: 확인 구간에서 같은 날씨였던 날의 코스피 '시가→종가' (우리 후보는 시가에 산다)
        20일 이상 · t ≤ −2 → '시가 매수 보류 권고' / t ≥ 2 → '시가 매수 유리했던 날씨' / 그 밖 → '규칙대로'
        예상 갭 ≥ +1% 날에는 기존 검증(갭상승 종목 시가 매수 하루 −1.11%)도 함께 표시
  시가 이후 흐름(시가→종가)을 예측한다는 근거는 없다 — 갭만 예상한다.
그 밖에 나스닥·필라델피아 반도체(SOX)·원달러·VIX 는 참고로만 보여 준다(모형에 안 씀).
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse

import numpy as np
import pandas as pd
import requests

from .config import CFG

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ai-trade-desk/0.1"}
REF = {"EWY": "EWY", "나스닥": "^IXIC", "S&P500": "^GSPC", "반도체(SOX)": "^SOX", "원달러": "KRW=X", "VIX": "^VIX"}
STOOQ = {"EWY": "ewy.us", "^IXIC": "^ndq", "^GSPC": "^spx", "KRW=X": "usdkrw", "^VIX": "^vix"}
CATS = [(0.01, "☀️", "맑음"), (0.003, "🌤", "구름 조금"), (-0.003, "☁️", "흐림"), (-0.01, "🌦", "비 조금"), (-9, "🌧", "비")]
OOS = 250


def _yahoo(sym: str, rng: str = "10y") -> pd.Series:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d"
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                off = res["meta"].get("gmtoffset", 0)
                d = pd.to_datetime(np.array(res["timestamp"]) + off, unit="s").normalize()
                c = res["indicators"]["quote"][0]["close"]
                s = pd.Series(c, index=d, dtype=float).dropna()
                return s[~s.index.duplicated(keep="last")]
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"yahoo {sym}")


def _stooq(sym: str) -> pd.Series:
    r = requests.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", headers=UA, timeout=20)
    df = pd.read_csv(pd.io.common.StringIO(r.text))
    return pd.Series(df["Close"].values, index=pd.to_datetime(df["Date"]), dtype=float)


def series(sym: str) -> pd.Series:
    try:
        return _yahoo(sym)
    except Exception:
        if sym in STOOQ:
            return _stooq(STOOQ[sym])
        raise


def cat(g: float):
    for th, ic, nm in CATS:
        if g >= th:
            return ic, nm
    return CATS[-1][1], CATS[-1][2]


def tstat(x):
    x = pd.Series(x).dropna()
    if len(x) < 3 or x.std() == 0:
        return None
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x)))


def build(kospi_ohlc: pd.DataFrame, today: str) -> dict:
    """kospi_ohlc: 코스피 일봉(open, close). today: 오늘 날짜(KST). 오늘 일봉은 있어도 쓰지 않는다."""
    k = kospi_ohlc[kospi_ohlc.index < pd.Timestamp(today)].copy()
    k["gap"] = k["open"] / k["close"].shift(1) - 1
    k["oc"] = k["close"] / k["open"] - 1
    ewy = series("EWY")
    er = ewy.pct_change().rename("ewy")
    # 한국 날짜 t 에는 t 이전(같은 날 제외)의 마지막 미국 종가가 반영된다 (미국 장은 한국 시각 새벽에 끝남)
    left = pd.DataFrame({"d": k.index}).sort_values("d")
    right = er.dropna().reset_index().rename(columns={"index": "u"}).sort_values("u")
    right.columns = ["u", "ewy"]
    m = pd.merge_asof(left, right, left_on="d", right_on="u", allow_exact_matches=False, direction="backward")
    k["ewy"] = m.set_index("d")["ewy"]
    k["us_day"] = m.set_index("d")["u"]
    d = k.dropna(subset=["gap", "oc", "ewy"])
    d = d[d["us_day"] >= d.index.to_series().shift(1).fillna(d.index[0])]  # 미국 휴장으로 이틀 묵은 값은 제외
    fit, test = d.iloc[:-OOS], d.iloc[-OOS:]
    b, a = np.polyfit(fit["ewy"], fit["gap"], 1)
    pred = a + b * test["ewy"]
    r = float(np.corrcoef(pred, test["gap"])[0, 1])
    hit = float(((pred > 0) == (test["gap"] > 0)).mean())
    mae = float((pred - test["gap"]).abs().mean())
    r_oc = float(np.corrcoef(pred, test["oc"])[0, 1])
    # 확인 구간의 날씨별 실제 결과
    t2 = test.assign(pred=pred, cat=[cat(x)[1] for x in pred])
    by = {}
    for nm in [c[2] for c in CATS]:
        g = t2[t2["cat"] == nm]
        by[nm] = {"n": int(len(g)), "gap": float(g["gap"].mean()) if len(g) else None,
                  "oc": float(g["oc"].mean()) if len(g) else None, "t_oc": tstat(g["oc"]),
                  "up_oc": float((g["oc"] > 0).mean()) if len(g) else None}
    # 오늘
    last_us = er.dropna()
    last_us = last_us[last_us.index < pd.Timestamp(today)]
    e_today = float(last_us.iloc[-1])
    g_today = float(a + b * e_today)
    icon, name = cat(g_today)
    ref = {}
    for lab, sym in REF.items():
        try:
            s = series(sym)
            s = s[s.index < pd.Timestamp(today)]
            ref[lab] = {"day": str(s.index[-1].date()), "close": float(s.iloc[-1]), "chg": float(s.iloc[-1] / s.iloc[-2] - 1)}
        except Exception as ex:
            ref[lab] = {"error": type(ex).__name__}
    vix = (ref.get("VIX") or {}).get("close")
    st = by[name]
    if st["n"] >= 20 and st["t_oc"] is not None and st["t_oc"] <= -2:
        verdict, level = f"시가 매수 보류 권고 — 확인 구간 '{name}' 날 코스피 시가→종가 평균 {st['oc'] * 100:+.2f}% (t {st['t_oc']:.2f}, {st['n']}일)", "hold"
    elif st["n"] >= 20 and st["t_oc"] is not None and st["t_oc"] >= 2:
        verdict, level = f"시가 매수 유리했던 날씨 — 확인 구간 '{name}' 날 시가→종가 평균 {st['oc'] * 100:+.2f}% (t {st['t_oc']:.2f}, {st['n']}일)", "go"
    else:
        verdict, level = (f"규칙대로 — 이런 날씨({name})의 시가→종가는 뚜렷한 차이 없음"
                          + (f" (평균 {st['oc'] * 100:+.2f}%, {st['n']}일)" if st["n"] else " (확인 구간 표본 없음)")), "normal"
    notes = []
    if g_today >= 0.01:
        notes.append("갭상승 예상 — 기존 검증: 갭상승 종목을 시가에 사면 하루 평균 −1.11%, 시가 추격 주의")
    if vix and vix >= 30:
        notes.append(f"⚡ VIX {vix:.1f} — 변동성 큼, 손절·비중 확인")
    out = {"date": today, "generated": (pd.Timestamp.now("UTC") + pd.Timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"), "icon": icon + (" ⚡" if vix and vix >= 30 else ""), "name": name, "pred_gap": g_today,
           "ewy": e_today, "ewy_day": str(last_us.index[-1].date()), "verdict": verdict, "level": level, "notes": notes,
           "model": {"a": float(a), "b": float(b), "fit_from": str(fit.index[0].date()), "fit_to": str(fit.index[-1].date()),
                     "test_from": str(test.index[0].date()), "test_to": str(test.index[-1].date()),
                     "r": r, "hit": hit, "mae": mae, "r_oc": r_oc, "n_test": int(len(test))},
           "by_weather": by, "ref": ref}
    # 기록 (나중에 날씨별 우리 목록 성적을 쌓기 위해)
    lp = CFG.state_dir / "weather_log.json"
    log = json.loads(lp.read_text()) if lp.exists() else {}
    log[today] = {"name": name, "pred_gap": g_today, "ewy": e_today}
    lp.write_text(json.dumps(log, ensure_ascii=False, indent=0))
    out["own"] = own_stats(log)
    return out


def own_stats(log: dict) -> dict:
    """기록된 날씨별 우리 목록(저녁 평가) 시가→종가 — 쌓이는 중인 자체 근거."""
    rp = CFG.state_dir / "eval_rows.json"
    if not rp.exists():
        return {}
    R = pd.DataFrame(json.loads(rp.read_text(encoding="utf-8")))
    if R.empty:
        return {}
    R["w"] = R["day"].map(lambda d: (log.get(d) or {}).get("name"))
    R = R.dropna(subset=["w"])
    out = {}
    for w, g in R.groupby("w"):
        daily = g.groupby("day")["plain"].mean()
        out[w] = {"days": int(len(daily)), "mean": float(daily.mean()), "t": tstat(daily)}
    return out
