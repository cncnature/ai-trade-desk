"""시세·수급 수집.

주력: 네이버증권 공개 페이지(무계정). 운영 단계에서는 KRX OpenAPI / KIS / 토스 API 로
교체할 수 있도록 `PriceSource` 인터페이스로 분리했다.

- 일봉 OHLCV : fchart.stock.naver.com (XML, 한 번에 N일)
- 투자자 수급 : finance.naver.com/item/frgn.naver (기관·외국인 순매매, 외국인 보유율)
- 유니버스    : finance.naver.com/sise/sise_market_sum.naver (시가총액 순)

모든 결과는 data/cache/*.csv 에 저장하고 다음 실행 때는 최근분만 덧붙인다.
"""
from __future__ import annotations

import os
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
        """시총 상위 종목. 경로 3개를 차례로 시도하고, 성공한 목록은 state/universe.json 에 남긴다."""
        import json

        seed = CFG.state_dir / "universe.json"
        for fn in (self._universe_mobile, self._universe_pc):
            try:
                out = fn(top_n)
                if len(out) >= top_n:  # 두 시장 합쳐 최소 top_n 이상이면 성공으로 봄
                    print(f"  유니버스: {fn.__name__} → {len(out)}종목", flush=True)
                    CFG.ensure_dirs()
                    seed.write_text(json.dumps([s.__dict__ for s in out], ensure_ascii=False))
                    return out
                print(f"  유니버스: {fn.__name__} 결과 {len(out)}종목 — 다음 경로 시도", flush=True)
            except Exception as e:
                print(f"  유니버스: {fn.__name__} 실패 {type(e).__name__}: {str(e)[:160]}", flush=True)
        if seed.exists():
            out = [Stock(**d) for d in json.loads(seed.read_text())]
            print(f"  유니버스: 저장된 목록 사용 {len(out)}종목", flush=True)
            return out
        return []

    def _universe_mobile(self, top_n: int) -> list[Stock]:
        import json

        out: list[Stock] = []
        for market in ("KOSPI", "KOSDAQ"):
            got: list[Stock] = []
            page = 1
            while len(got) < top_n and page <= 10:
                j = json.loads(self._get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                                         {"page": page, "pageSize": 100}))
                rows = j.get("stocks", [])
                if not rows:
                    break
                for r in rows:
                    code, name = r.get("itemCode", ""), r.get("stockName", "")
                    if not re.fullmatch(r"\d{6}", code) or "스팩" in name or "ETN" in name:
                        continue
                    got.append(Stock(code, name, market))
                page += 1
            out.extend(got[:top_n])
        return out

    def _universe_pc(self, top_n: int) -> list[Stock]:
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
                rows = re.findall(r'/item/main\.naver\?code=(\d{6})"[^>]*class="tltle"[^>]*>([^<]+)</a>', html)
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
        return clean_ohlcv(df.set_index("date").sort_index())

    def flow(self, code: str, pages: int) -> pd.DataFrame:
        """기관·외국인 순매매량(주), 외국인 보유율(%). PC 페이지 → 모바일 API 순으로 시도."""
        try:
            df = self._flow_pc(code, pages)
            if len(df):
                return df
        except Exception:
            pass
        return self._flow_mobile(code, pages * 20)

    def _flow_mobile(self, code: str, n: int) -> pd.DataFrame:
        import json

        j = json.loads(self._get(f"https://m.stock.naver.com/api/stock/{code}/trend", {"pageSize": min(n, 200)}))
        rows = j if isinstance(j, list) else j.get("trends") or j.get("result") or []

        def pick(r, *keys):
            for k in keys:
                if k in r and r[k] not in (None, ""):
                    return float(str(r[k]).replace(",", "").replace("%", "").replace("+", ""))
            return float("nan")

        recs = []
        for r in rows:
            dt = str(r.get("bizdate") or r.get("localTradedAt") or "")[:10].replace("-", "")
            if not re.fullmatch(r"\d{8}", dt):
                continue
            recs.append({"date": pd.Timestamp(dt),
                         "inst_net": pick(r, "organPureBuyQuant", "institutionPureBuyQuant"),
                         "frgn_net": pick(r, "foreignerPureBuyQuant", "foreignPureBuyQuant"),
                         "frgn_hold_pct": pick(r, "foreignerHoldRatio", "foreignHoldRatio")})
        if not recs:
            return pd.DataFrame(columns=["inst_net", "frgn_net", "frgn_hold_pct"], dtype=float)
        return pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()

    def _flow_pc(self, code: str, pages: int) -> pd.DataFrame:
        from bs4 import BeautifulSoup

        recs = []
        for page in range(1, pages + 1):
            self.s.headers["Referer"] = f"https://finance.naver.com/item/main.naver?code={code}"
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


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """거래정지일 등 시가·고가·저가가 0 으로 오는 행을 종가로 메우고, 종가가 0 이하인 행은 버린다."""
    df = df[df["close"] > 0].copy()
    for col in ("open", "high", "low"):
        bad = df[col] <= 0
        df.loc[bad, col] = df.loc[bad, "close"]
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df


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


FLOW_FILE = CFG.cache_dir.parent / "flow" / "flow.csv.gz"
_FLOW_STORE: dict[str, pd.DataFrame] | None = None
_FLOW_LOADED = False


def flow_store() -> dict[str, pd.DataFrame] | None:
    """서버 PC(export_flow.py)가 올린 수급 파일을 종목별로 나눠 한 번만 읽는다.
    형식: date,code,inst_net,frgn_net,frgn_hold_pct  (gzip CSV)"""
    global _FLOW_STORE, _FLOW_LOADED
    if _FLOW_LOADED:
        return _FLOW_STORE
    _FLOW_LOADED = True
    if not FLOW_FILE.exists():
        return None
    parts = [pd.read_csv(FLOW_FILE, dtype={"code": str}, parse_dates=["date"])]
    for p in sorted((FLOW_FILE.parent / "daily").glob("*.csv")):  # 기본 파일 이후 매일 추가분
        parts.append(pd.read_csv(p, dtype={"code": str}, parse_dates=["date"]))
    df = pd.concat(parts, ignore_index=True)
    df["code"] = df["code"].str.zfill(6)
    _FLOW_STORE = {c: g.drop(columns="code").drop_duplicates("date").set_index("date").sort_index()
                   for c, g in df.groupby("code")}
    print(f"  수급 파일: {len(_FLOW_STORE)}종목, {df['date'].min().date()}~{df['date'].max().date()}", flush=True)
    return _FLOW_STORE


def load_stock(src, code: str, history: int) -> pd.DataFrame:
    """가격 + 수급을 하나의 일별 표로. 캐시가 있으면 최근분만 받아 덧붙인다."""
    CFG.ensure_dirs()
    p_path = CFG.cache_dir / f"{code}_px.csv"
    f_path = CFG.cache_dir / f"{code}_flow.csv"
    synthetic = isinstance(src, SyntheticSource)

    def _rows(path: Path) -> int:  # 캐시가 비었거나 짧으면 처음부터 다시 받는다
        try:
            return sum(1 for _ in open(path, encoding="utf-8")) - 1
        except OSError:
            return 0

    px_full = synthetic or _rows(p_path) < history - 30
    px = src.ohlcv(code, history if px_full else 30)
    if not synthetic:
        px = clean_ohlcv(_merge_cache(p_path, px))

    # 수급: ① 서버 PC가 올린 data/flow/flow.csv.gz  ② (옵션) 네이버 직접 수집
    store = flow_store()
    empty = pd.DataFrame(columns=["inst_net", "frgn_net", "frgn_hold_pct"], dtype=float)
    if synthetic:
        try:
            fl = src.flow(code, max(1, history // 20))
        except Exception:
            fl = empty
    elif store is not None and code in store:
        fl = store[code]
    elif os.environ.get("DESK_NAVER_FLOW") == "1":
        fl_full = _rows(f_path) < 200
        try:
            fl = src.flow(code, max(1, history // 20) if fl_full else 1)
        except Exception:  # 수급 실패는 종목을 버리지 않는다 (수급 팩터만 비게 됨)
            fl = pd.DataFrame(columns=["inst_net", "frgn_net", "frgn_hold_pct"], dtype=float)
        if len(fl):
            fl = _merge_cache(f_path, fl)
        elif f_path.exists():
            fl = pd.read_csv(f_path, index_col=0, parse_dates=True)
    else:
        fl = pd.DataFrame(columns=["inst_net", "frgn_net", "frgn_hold_pct"], dtype=float)
    df = px.join(fl, how="left")
    # 수급 자료가 있는 기간 안에서만 빈 날을 0으로 채운다. 자료가 아예 없으면 NaN 그대로 둬서
    # 수급 팩터가 '0' 이 아니라 '자료 없음' 으로 처리되게 한다.
    if len(fl):
        inside = df.index >= fl.index.min()
        df.loc[inside, ["inst_net", "frgn_net"]] = df.loc[inside, ["inst_net", "frgn_net"]].fillna(0.0)
        df["frgn_hold_pct"] = df["frgn_hold_pct"].ffill()
    return df.tail(history)


def load_index(src, symbol: str, history: int) -> pd.Series:
    return src.ohlcv(symbol, history)["close"]


def diagnose() -> None:
    """각 데이터 주소의 응답을 찍는다. GitHub Actions 처럼 해외 서버에서 막히는지 확인용."""
    import requests

    tests = {
        "모바일 시총 API": ("https://m.stock.naver.com/api/stocks/marketValue/KOSPI", {"page": 1, "pageSize": 5}),
        "PC 시총 페이지": ("https://finance.naver.com/sise/sise_market_sum.naver", {"sosok": 0, "page": 1}),
        "일봉 fchart": ("https://fchart.stock.naver.com/sise.nhn",
                        {"symbol": "005930", "timeframe": "day", "count": 5, "requestType": 0}),
        "수급 frgn": ("https://finance.naver.com/item/frgn.naver", {"code": "005930", "page": 1}),
        "수급 모바일": ("https://m.stock.naver.com/api/stock/005930/trend", {"pageSize": 5}),
    }
    for name, (url, params) in tests.items():
        try:
            r = requests.get(url, params=params, headers=UA, timeout=15)
            body = r.text
            hint = "삼성전자" in r.content.decode("euc-kr", "ignore") or "005930" in body or "삼성전자" in body
            print(f"[점검] {name}: HTTP {r.status_code}, {len(r.content)}B, 삼성전자 포함={hint}")
        except Exception as e:
            print(f"[점검] {name}: 실패 {type(e).__name__}: {str(e)[:120]}")
