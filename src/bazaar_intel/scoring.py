"""امتیاز صفر تا صد با سقف‌های ثابت. داده ناقص امتیاز را بالا نمی‌برد."""

from __future__ import annotations

from dataclasses import dataclass

LIMITS = {
    "evidence": 20,
    "forward": 20,
    "earnings": 20,
    "price_gap": 15,
    "risk_reward": 15,
    "timing": 10,
}


@dataclass(frozen=True)
class Score:
    evidence: float
    forward: float
    earnings: float
    price_gap: float
    risk_reward: float
    timing: float
    total: float
    band: str
    confidence: str
    rationale: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "evidence": self.evidence,
            "forward": self.forward,
            "earnings": self.earnings,
            "price_gap": self.price_gap,
            "risk_reward": self.risk_reward,
            "timing": self.timing,
            "total": self.total,
            "band": self.band,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


def band_for(total: float) -> str:
    if total >= 85:
        return "فرصت بسیار مهم"
    if total >= 70:
        return "فرصت قابل بررسی جدی"
    if total >= 55:
        return "فرصت احتمالی"
    if total >= 40:
        return "سرنخ نیازمند بررسی"
    return "فعلاً فاقد جذابیت کافی"


def score_opportunity(
    *,
    evidence: float,
    forward: float,
    earnings: float,
    price_gap: float,
    risk_reward: float,
    timing: float,
    rationale: str,
    missing_critical: bool,
) -> Score:
    parts = {
        "evidence": evidence,
        "forward": forward,
        "earnings": earnings,
        "price_gap": price_gap,
        "risk_reward": risk_reward,
        "timing": timing,
    }
    for name, value in parts.items():
        limit = LIMITS[name]
        if value < 0 or value > limit:
            raise ValueError(f"{name} must be between 0 and {limit}")
    total = round(sum(parts.values()), 2)
    confidence = "low" if missing_critical or total < 40 else "medium"
    return Score(
        evidence=evidence,
        forward=forward,
        earnings=earnings,
        price_gap=price_gap,
        risk_reward=risk_reward,
        timing=timing,
        total=total,
        band=band_for(total),
        confidence=confidence,
        rationale=rationale,
    )
