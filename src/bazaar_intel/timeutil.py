"""زمان تهران و تبدیل تاریخ. زمان منبع اگر منطقه ندارد، منطقه به آن نسبت داده نمی‌شود."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def tehran_now() -> datetime:
    return utc_now().astimezone(TEHRAN)


def iso_utc(moment: datetime | None = None) -> str:
    moment = moment or utc_now()
    if moment.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def tehran_label(moment: datetime | None = None) -> str:
    moment = (moment or utc_now()).astimezone(TEHRAN).replace(microsecond=0)
    return moment.strftime("%Y-%m-%d %H:%M") + " تهران"


def is_trading_weekday(moment: datetime | None = None) -> bool:
    moment = (moment or utc_now()).astimezone(TEHRAN)
    return moment.weekday() in {5, 6, 0, 1, 2}


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    month_lengths = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > month_lengths[gm]:
        gd -= month_lengths[gm]
        gm += 1
    return gy, gm, gd


def jalali_label(moment: datetime | None = None) -> str:
    moment = (moment or utc_now()).astimezone(TEHRAN)
    jy, jm, jd = gregorian_to_jalali(moment.year, moment.month, moment.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def jalali_date_to_tehran_midnight_utc(text: str) -> str | None:
    """تاریخ شمسی را به نیمه‌شب تهران همان روز وصل می‌کند و این قرارداد را برمی‌گرداند."""
    raw = text.strip().replace("-", "/")
    parts = raw.split("/")
    if len(parts) != 3:
        return None
    try:
        jy, jm, jd = (int(p) for p in parts)
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
    except (ValueError, OverflowError):
        return None
    local = datetime(gy, gm, gd, 0, 0, tzinfo=TEHRAN)
    return local.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {value}")
    return moment


def seconds_until(hour: int, minute: int, *, weekdays: set[int] | None = None) -> float:
    now = tehran_now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    if weekdays is not None:
        while target.weekday() not in weekdays:
            target += timedelta(days=1)
    return (target - now).total_seconds()
