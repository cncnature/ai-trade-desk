"""모의매매 장부 (state/paper_ledger.json).

체결 규칙은 검증(validate.py)과 똑같이 맞췄다 — 검증 성적과 모의 성적이 같은 규칙으로 비교되어야 하므로.
- 신호일 t 장 마감 후 주문 생성 → t+1 시가 체결
- 손절: 저가 ≤ 손절가 → min(시가, 손절가) 체결 (갭하락 반영)
- 목표: 고가 ≥ 목표가 → max(시가, 목표가) 체결. 같은 날 둘 다 닿으면 손절로 본다(보수적)
- 시간청산: 보유 hold_days 거래일 째 종가
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import CFG


class PaperBroker:
    name = "paper"

    def __init__(self, path: Path | None = None, equity: float | None = None):
        self.path = path or (CFG.state_dir / "paper_ledger.json")
        if self.path.exists():
            self.s = json.loads(self.path.read_text())
        else:
            eq = equity or CFG.equity
            self.s = {"cash": eq, "start_equity": eq, "positions": {}, "pending": [], "closed": [], "curve": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.s, ensure_ascii=False, indent=1))

    # ── 하루 처리 (bars: code → {date, open, high, low, close}) ──
    def process_day(self, day: str, bars: dict[str, dict], cost: float) -> list[dict]:
        events = []
        half = cost / 2
        # 1) 어제 만든 주문을 오늘 시가로 체결
        for o in self.s["pending"]:
            b = bars.get(o["code"])
            if not b or o["code"] in self.s["positions"]:
                continue
            px = b["open"]
            qty = min(o["qty"], int(self.s["cash"] // (px * (1 + half))))
            if qty <= 0:
                continue
            self.s["cash"] -= qty * px * (1 + half)
            self.s["positions"][o["code"]] = {
                "name": o["name"], "qty": qty, "entry": px, "entry_date": day, "days": 0,
                "stop": px * o["stop"] / o["entry_ref"], "target": px * o["target"] / o["entry_ref"],
                "hold_days": o["hold_days"],
            }
            events.append({"type": "BUY", "code": o["code"], "name": o["name"], "qty": qty, "price": round(px)})
        self.s["pending"] = []

        # 2) 보유 종목 청산 판정
        for code, p in list(self.s["positions"].items()):
            b = bars.get(code)
            if not b:
                continue
            p["days"] += 1
            exit_px, why = None, None
            if b["low"] <= p["stop"]:
                exit_px, why = min(b["open"], p["stop"]), "손절"
            elif b["high"] >= p["target"]:
                exit_px, why = max(b["open"], p["target"]), "목표"
            elif p["days"] >= p["hold_days"]:
                exit_px, why = b["close"], "시간청산"
            if exit_px is not None:
                self.s["cash"] += p["qty"] * exit_px * (1 - half)
                pnl = exit_px * (1 - half) / (p["entry"] * (1 + half)) - 1
                rec = {"code": code, "name": p["name"], "entry_date": p["entry_date"], "exit_date": day,
                       "entry": round(p["entry"]), "exit": round(exit_px), "qty": p["qty"],
                       "ret_pct": round(pnl * 100, 2), "reason": why}
                self.s["closed"].append(rec)
                events.append({"type": "SELL", **rec})
                del self.s["positions"][code]
            else:
                p["last"] = b["close"]

        self.s["curve"].append({"date": day, "equity": round(self.equity(bars))})
        return events

    def equity(self, bars: dict | None = None) -> float:
        val = self.s["cash"]
        for code, p in self.s["positions"].items():
            px = (bars or {}).get(code, {}).get("close") or p.get("last") or p["entry"]
            val += p["qty"] * px
        return val

    def day_return(self) -> float:
        c = self.s["curve"]
        return c[-1]["equity"] / c[-2]["equity"] - 1 if len(c) >= 2 else 0.0

    def queue(self, picks: list[dict]) -> int:
        """내일 시가 주문 등록. 이미 보유 중이거나 자리가 없으면 건너뜀."""
        if self.day_return() <= -CFG.daily_loss_limit:
            return 0
        slots = CFG.max_positions - len(self.s["positions"])
        n = 0
        for p in picks:
            if n >= slots:
                break
            if p["code"] in self.s["positions"] or p["qty"] <= 0:
                continue
            self.s["pending"].append({k: p[k] for k in ("code", "name", "qty", "entry_ref", "stop", "target", "hold_days")})
            n += 1
        return n

    def summary(self) -> dict:
        closed = self.s["closed"]
        wins = [c for c in closed if c["ret_pct"] > 0]
        eq = self.s["curve"][-1]["equity"] if self.s["curve"] else self.s["start_equity"]
        return {
            "equity": round(eq), "start_equity": self.s["start_equity"],
            "total_ret_pct": round((eq / self.s["start_equity"] - 1) * 100, 2),
            "trades": len(closed), "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "avg_ret_pct": round(sum(c["ret_pct"] for c in closed) / len(closed), 2) if closed else None,
            "positions": self.s["positions"], "pending": self.s["pending"],
            "recent": closed[-15:][::-1], "curve": self.s["curve"][-250:],
        }
