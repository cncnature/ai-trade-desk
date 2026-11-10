"""전역 설정. 모든 값은 환경변수로 덮어쓸 수 있다 (예: DESK_TOP_N=150)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return cast(raw)


@dataclass
class Config:
    # ── 데이터 ─────────────────────────────────────────────
    data_source: str = field(default_factory=lambda: _env("DESK_DATA", "naver"))  # naver | synthetic
    top_n_per_market: int = field(default_factory=lambda: _env("DESK_TOP_N", 100, int))  # 코스피·코스닥 각각 시총 상위 N
    # top: 시총 상위 N (네이버) | flow: 서버 PC 수급 파일에 있는 전 종목 (거래대금 3억 미만은 이미 제외됨)
    universe_mode: str = field(default_factory=lambda: _env("DESK_UNIVERSE", "top"))
    history_days: int = field(default_factory=lambda: _env("DESK_HISTORY", 1500, int))
    cache_dir: Path = ROOT / "data" / "cache"
    state_dir: Path = ROOT / "state"
    docs_data_dir: Path = ROOT / "docs" / "data"

    # ── 검증 게이트 (차별점: 검증을 통과한 팩터만 점수에 투표) ──
    hold_days: int = field(default_factory=lambda: _env("DESK_HOLD", 10, int))
    explore_frac: float = 0.70           # 앞 70% 탐색, 뒤 30% 확인
    explore_t_min: float = 2.0           # 탐색 구간 t ≥ 2.0
    confirm_t_min: float = 1.5           # 확인 구간 t ≥ 1.5 이고 평균 > 0
    top_quantile: float = 0.20           # 팩터 상위 20% 바스켓
    liq_cut: float = field(default_factory=lambda: _env("DESK_LIQ_CUT", 0.2, float))  # 날짜별 거래대금 하위 비율 제외
    # 사전 등록 검정 대상(쉼표 구분). 비우면 전 팩터를 검정. 대상이 아닌 팩터는 '관찰용'으로 수치만 표시.
    test_factors: str = field(default_factory=lambda: _env("DESK_FACTORS", ""))
    cost_roundtrip: float = field(default_factory=lambda: _env("DESK_COST", 0.0025, float))  # 수수료+세금 왕복

    # ── 리스크 ─────────────────────────────────────────────
    equity: float = field(default_factory=lambda: _env("DESK_EQUITY", 200_000_000, float))  # 모의 계좌 시작 자산 2억
    risk_per_trade: float = 0.01         # 손절 시 자본의 1%
    max_weight: float = 0.15             # 종목당 최대 비중 (금액 상한과 함께 적용 — 둘 중 작은 쪽)
    max_position_krw: float = field(default_factory=lambda: _env("DESK_MAX_POS_KRW", 10_000_000, float))  # 종목당 최대 1,000만 원

    def position_cap(self, equity: float) -> float:
        return min(equity * self.max_weight, self.max_position_krw)

    max_positions: int = field(default_factory=lambda: _env("DESK_MAX_POS", 20, int))
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 3.0
    daily_loss_limit: float = 0.03       # 하루 -3% 넘으면 신규 매수 중지

    # ── 실행 ───────────────────────────────────────────────
    mode: str = field(default_factory=lambda: _env("DESK_MODE", "paper"))  # paper | kis_mock | live
    live_confirm: str = field(default_factory=lambda: _env("DESK_LIVE_CONFIRM", ""))

    # ── 알림 / AI 설명 (판정에는 쓰지 않음) ───────────────────
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))
    ai_provider: str = field(default_factory=lambda: _env("DESK_AI", "off"))  # off | anthropic | ollama
    ai_model: str = field(default_factory=lambda: _env("DESK_AI_MODEL", "claude-haiku-4-5"))

    def tested(self, all_factors) -> list[str]:
        want = [f.strip() for f in self.test_factors.split(",") if f.strip()]
        return [f for f in all_factors if f in want] if want else list(all_factors)

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.state_dir, self.docs_data_dir):
            d.mkdir(parents=True, exist_ok=True)


CFG = Config()
