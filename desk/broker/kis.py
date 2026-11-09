"""한국투자증권(KIS) Open API 주문 어댑터 — 모의투자(kis_mock) / 실전(live).

⚠️ 실전 주문은 이중 잠금:
   DESK_MODE=live  그리고  DESK_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY
   둘 중 하나라도 없으면 주문 함수가 예외를 낸다.

필요 환경변수: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT(8자리), KIS_ACCOUNT_PROD(기본 01)
tr_id 는 KIS 공지(2024 주문 TR 변경) 기준: 매수 TTTC0012U / 매도 TTTC0011U, 모의는 V로 시작.
운영 전 KIS 개발자센터 최신 문서로 tr_id 를 한 번 더 확인할 것.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import requests

from ..config import CFG

DOMAINS = {
    "kis_mock": "https://openapivts.koreainvestment.com:29443",
    "live": "https://openapi.koreainvestment.com:9443",
}
LIVE_PHRASE = "I_UNDERSTAND_REAL_MONEY"


class KISBroker:
    def __init__(self, mode: str | None = None):
        self.mode = mode or CFG.mode
        if self.mode not in DOMAINS:
            raise ValueError(f"KIS 모드는 kis_mock 또는 live 여야 합니다: {self.mode}")
        self.base = DOMAINS[self.mode]
        self.key = os.environ["KIS_APP_KEY"]
        self.secret = os.environ["KIS_APP_SECRET"]
        self.cano = os.environ["KIS_ACCOUNT"]
        self.prod = os.environ.get("KIS_ACCOUNT_PROD", "01")
        self.token_path = CFG.state_dir / f"kis_token_{self.mode}.json"
        self.name = f"kis:{self.mode}"

    # ── 인증 (토큰은 하루 유효, 재발급 횟수 제한이 있어 파일로 재사용) ──
    def _token(self) -> str:
        if self.token_path.exists():
            t = json.loads(self.token_path.read_text())
            if datetime.fromisoformat(t["exp"]) > datetime.now() + timedelta(minutes=10):
                return t["access_token"]
        r = requests.post(f"{self.base}/oauth2/tokenP", timeout=10,
                          json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        r.raise_for_status()
        j = r.json()
        exp = datetime.now() + timedelta(seconds=int(j.get("expires_in", 86400)))
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps({"access_token": j["access_token"], "exp": exp.isoformat()}))
        return j["access_token"]

    def _h(self, tr_id: str) -> dict:
        return {"authorization": f"Bearer {self._token()}", "appkey": self.key, "appsecret": self.secret,
                "tr_id": tr_id, "custtype": "P", "content-type": "application/json; charset=utf-8"}

    def _tr(self, real: str) -> str:
        return real if self.mode == "live" else "V" + real[1:]

    # ── 조회 ──
    def price(self, code: str) -> dict:
        r = requests.get(f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price", timeout=10,
                         headers=self._h("FHKST01010100"),
                         params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        r.raise_for_status()
        o = r.json().get("output", {})
        return {"price": float(o.get("stck_prpr", 0)), "open": float(o.get("stck_oprc", 0)),
                "high": float(o.get("stck_hgpr", 0)), "low": float(o.get("stck_lwpr", 0))}

    def balance(self) -> dict:
        r = requests.get(f"{self.base}/uapi/domestic-stock/v1/trading/inquire-balance", timeout=10,
                         headers=self._h(self._tr("TTTC8434R")),
                         params={"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                                 "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                                 "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "",
                                 "CTX_AREA_NK100": ""})
        r.raise_for_status()
        j = r.json()
        holds = {h["pdno"]: int(h["hldg_qty"]) for h in j.get("output1", []) if int(h.get("hldg_qty", 0)) > 0}
        cash = float((j.get("output2") or [{}])[0].get("dnca_tot_amt", 0))
        return {"cash": cash, "holdings": holds}

    # ── 주문 ──
    def _guard(self) -> None:
        if self.mode == "live" and CFG.live_confirm != LIVE_PHRASE:
            raise PermissionError("실전 주문 잠금: DESK_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY 가 설정되지 않았습니다.")

    def order(self, code: str, qty: int, side: str, price: int = 0) -> dict:
        """side: buy|sell, price=0 이면 시장가."""
        self._guard()
        tr = self._tr("TTTC0012U" if side == "buy" else "TTTC0011U")
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "PDNO": code,
                "ORD_DVSN": "01" if price == 0 else "00", "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price))}
        r = requests.post(f"{self.base}/uapi/domestic-stock/v1/trading/order-cash", timeout=10,
                          headers=self._h(tr), json=body)
        j = r.json()
        time.sleep(0.25)  # 초당 호출 제한
        return {"ok": j.get("rt_cd") == "0", "msg": j.get("msg1", ""), "order_no": (j.get("output") or {}).get("ODNO")}
