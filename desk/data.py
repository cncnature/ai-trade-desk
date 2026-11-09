"""시세·수급 수집.

주력: 네이버증권 공개 페이지(무계정). 운영 단계에서는 KRX OpenAPI / KIS / 토스 API 로
교체할 수 있도록 `PriceSource` 인터페이스로 분리했다.

- 일봉 OHLCV : fchart.stock.naver.com (XML, 한 번에 N일)
- 투자자 수급 : finance.naver.com/item/frgn.naver (기관·외국인 순매매, 외국인 보유율)
- 유니버스    : finance.naver.com/sise/sise_market_sum.naver (시가총액 순)

모든 결과는 data/cache/*.csv 에 저장하고 다음 실행 때는 최근분만 덧붙인다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CFG

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ai-trade-desk/0.1"}
DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


@dataclass
class Stock:
    code: str
    name: str
    market: str  # KOSPI | KOSDAQ


# ──────────────────────────────────────────────────────────────
# 네이버 소스
# ──────────────────────────────────────────────────────────────
class NaverSource:
    def __init__(self, pause: float = 0.15):
        import requests

        self.s = requests.Session()
        self.s.headers.update(UA)
        self.pause = pause

    def _get(self, url: str, params: dict | None = None, encoding: str | None = None) -> str:
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, timeout=15)
                r.raise_for_status()
                if encoding:
                    r.encoding = encoding
                time.sleep(self.pause)
                return r.text
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        return ""

    def universe(self, top_n: int) -> list[Stock]:
        out: list[Stock] = []
        for sosok, market in ((0, "KOSPI"), (1, "KOSDAQ")):
            got: list[Stock] = []
            page = 1
            while len(got) < top_n and page <= 40:
                html = self._get(
                    "https://finance.naver.com/sise/sise_market_sum.naver",
                    {"sosok": sosok, "page": page},
                    encoding="euc-kr",
                )
                rows = re.findall(r'href="/item/main\.naver\?code=(\d{6})" class="tltle">([^<]+)</a>', html)
                if not rows:
                    break
                for code, name in rows:
                    if "스팩" in name or "ETN" in name:
                        continue
                    got.append(Stock(code, name.strip(), market))
                page += 1
            out.extend(got[:top_n])
        return out

    def ohlcv(self, symbol: str, count: int) -> pd.DataFrame:
        xml = self._get(
            "https://fchart.stock.naver.com/sise.nhn",
            {"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
            encoding="euc-kr",
        )
        rows = re.findall(r'data="([^"]+)"', xml)
        recs = []
        for row in rows:
            p = row.split("|")
            if len(p) < 6:
                continue
            recs.append((p[0], *map(float, p[1:6])))
        df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df.set_index("date").sort_index()

    def flow(self, code: str, pages: int) -> pd.DataFrame:
        """기관·외국인 순매매량(주), 외국인 보유율(%)."""
        from bs4 import BeautifulSoup

        recs = []
        for page in range(1, pages + 1):
            html = self._get(
                "https://finance.naver.com/item/frgn.naver", {"code": code, "page": page}, encoding="euc-kr"
            )
            soup = BeautifulSoup(html, "html.parser")
            n_before = len(recs)
            for tr in soup.select("table.type2 tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) < 9 or not DATE_RE.match(tds[0]):
                    continue
                y, m, d = DATE_RE.match(tds[0]).groups()
                num = lambda s: float(s.replace(",", "").replace("%", "").replace("+", "") or 0)  # noqa: E731
                recs.append(
                    {
                        "date": pd.Timestamp(int(y), int(m), int(d)),
                        "inst_net": num(tds[5]),
                        "frgn_net": num(tds[6]),
                        "frgn_hold_pct": num(tds[8]),
                    }
                )
            if len(recs) == n_before:
                break
        if not recs:
            return pd.DataFrame(columns=["inst_net", "frgn_net", "frgn_hold_pct"])
        return pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()


# ──────────────────────────────────────────────────────────────
# 합성 소스 (오프라인 테스트용 — 실제 시세 아님)
# ──────────────────────────────────────────────────────────────
class SyntheticSource:
    """외국인 보유율이 높을수록 아주 약한 초과수익이 있도록 심어 둔 가짜 시장.
    파이프라인이 '심어 둔 효과는 찾고, 노이즈 팩터는 거르는지' 확인하는 용도."""

    def __init__(self, seed: int = 7, n_days: int = 600, effect: float | None = None):
        import os

        self.rng = np.random.default_rng(seed)
        self.effect = effect if effect is not None else float(os.environ.get("DESK_SYN_EFFECT", "0.00002"))
        self.dates = pd.bdate_range(end=pd.Timestamp("2026-11-09"), periods=n_days)
        self._cache: dict[str, pd.DataFrame] = {}

    def universe(self, top_n: int) -> list[Stock]:
        return [Stock(f"{i:06d}", f"가상종목{i:03d}", "KOSPI" if i % 2 else "KOSDAQ") for i in range(1, 2 * top_n + 1)]

    def _make(self, code: str) -> pd.DataFrame:
        if code in self._cache:
            return self._cache[code]
        rng = np.random.default_rng(int(code))
        n = len(self.dates)
        hold = np.clip(rng.uniform(2, 50) + np.cumsum(rng.normal(0, 0.05, n)), 0, 90)
        drift = self.effect * (hold - 25)  # 심어 둔 효과
        ret = drift + rng.normal(0, 0.022, n)
        close = 10000 * np.exp(np.cumsum(ret))
        openp = close * np.exp(rng.normal(0, 0.006, n))
        high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
        low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
        vol = rng.lognormal(12, 0.5, n)
        df = pd.DataFrame(
            {
                "open": openp, "high": high, "low": low, "close": close, "volume": vol,
                "inst_net": rng.normal(0, 1, n) * vol * 0.05,
                "frgn_net": rng.normal(0, 1, n) * vol * 0.05,
                "frgn_hold_pct": hold,
            },
            index=self.dates,
        )
        self._cache[code] = df
        return df

    def ohlcv(self, symbol: str, count: int) -> pd.DataFrame:
        if symbol in ("KOSPI", "KOSDAQ"):
            rng = np.random.default_rng(1 if symbol == "KOSPI" else 2)
            c = 2500 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(self.dates))))
            return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=self.dates).tail(count)
        return self._make(symbol)[["open", "high", "low", "close", "volume"]].tail(count)

    def flow(self, code: str, pages: int) -> pd.DataFrame:
        return self._make(code)[["inst_net", "frgn_net", "frgn_hold_pct"]].tail(pages * 20)


def get_source():
    return SyntheticSource() if CFG.data_source == "synthetic" else NaverSource()


# ──────────────────────────────────────────────────────────────
# 캐시 병합
# ──────────────────────────────────────────────────────────────
def _merge_cache(path: Path, fresh: pd.DataFrame) -> pd.DataFrame:
    if path.exists():
        old = pd.read_csv(path, index_col=0, parse_dates=True)
        fresh = pd.concat([old, fresh])
        fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
    fresh.to_csv(path)
    return fresh


def load_stock(src, code: str, history: int) -> pd.DataFrame:
    """가격 + 수급을 하나의 일별 표로. 캐시가 있으면 최근분만 받아 덧붙인다."""
    CFG.ensure_dirs()
    p_path = CFG.cache_dir / f"{code}_px.csv"
    f_path = CFG.cache_dir / f"{code}_flow.csv"
    synthetic = isinstance(src, SyntheticSource)
    px = src.ohlcv(code, 30 if (p_path.exists() and not synthetic) else history)
    fl = src.flow(code, 1 if (f_path.exists() and not synthetic) else max(1, history // 20))
    if not synthetic:
        px = _merge_cache(p_path, px)
        fl = _merge_cache(f_path, fl)
    df = px.join(fl, how="left")
    df[["inst_net", "frgn_net"]] = df[["inst_net", "frgn_net"]].fillna(0.0)
    df["frgn_hold_pct"] = df["frgn_hold_pct"].ffill()
    return df.tail(history)


def load_index(src, symbol: str, history: int) -> pd.Series:
    return src.ohlcv(symbol, history)["close"]
