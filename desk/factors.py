"""팩터 계산. 모든 값은 해당일 종가까지의 정보만 쓴다(미래 정보 없음).

각 팩터는 '값이 클수록 좋다'는 가설 방향으로 정의했다. 맞는지는 validate.py 가 판정한다.
근거 문헌은 README 의 References 참고.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS: dict[str, str] = {
    "frgn_hold":     "외국인 보유율 수준",
    "frgn_hold_chg": "외국인 보유율 20일 변화",
    "flow_5d":       "외국인+기관 5일 순매수 / 20일 평균거래량",
    "mom_12_1":      "모멘텀(65~5일 전 수익률)",
    "trend":         "추세(20일선/60일선 − 1)",
    "pos_52w":       "52주 범위 내 위치",
    "low_vol":       "저변동성(−20일 변동성)",
    "vol_surge":     "거래량 급증(당일/20일 평균)",
    "vcp":           "변동성 수축(VCP 근사)",
}


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def compute(df: pd.DataFrame) -> pd.DataFrame:
    c, v = df["close"], df["volume"]
    out = pd.DataFrame(index=df.index)
    ret = c.pct_change()
    vol20 = v.rolling(20, min_periods=20).mean()

    out["frgn_hold"] = df["frgn_hold_pct"]
    out["frgn_hold_chg"] = df["frgn_hold_pct"] - df["frgn_hold_pct"].shift(20)
    out["flow_5d"] = (df["frgn_net"] + df["inst_net"]).rolling(5).sum() / vol20
    out["mom_12_1"] = c.shift(5) / c.shift(65) - 1
    out["trend"] = c.rolling(20).mean() / c.rolling(60).mean() - 1
    hi, lo = c.rolling(252, min_periods=120).max(), c.rolling(252, min_periods=120).min()
    out["pos_52w"] = (c - lo) / (hi - lo).replace(0, np.nan)
    out["low_vol"] = -ret.rolling(20).std()
    out["vol_surge"] = v / vol20
    a10, a50 = atr(df, 10), atr(df, 50)
    out["vcp"] = (1 - a10 / a50).where(out["pos_52w"] >= 0.7)

    # 리스크·표시용 (팩터 아님)
    out["atr_pct"] = atr(df, 14) / c
    out["close"] = c
    out["value_traded"] = (c * v).rolling(20).mean()
    return out


def forward_return(df: pd.DataFrame, hold: int, cost: float) -> pd.Series:
    """신호일 t → t+1 시가 매수, t+hold 종가 매도. 비용 차감."""
    return df["close"].shift(-hold) / df["open"].shift(-1) - 1 - cost


def build_panel(frames: dict[str, pd.DataFrame], hold: int, cost: float) -> pd.DataFrame:
    parts = []
    for code, df in frames.items():
        if len(df) < 130:
            continue
        f = compute(df)
        f["fwd"] = forward_return(df, hold, cost)
        f["code"] = code
        parts.append(f)
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts)
    panel.index.name = "date"
    return panel.reset_index()
