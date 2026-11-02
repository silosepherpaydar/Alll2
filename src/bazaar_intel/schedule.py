"""نزدیک‌ترین اجرای قابل اتکا به ۸:۵۰ و ۱۹:۰۰ تهران.

اگر این فرآیند متوقف شود، گزارش همان ساعت ساخته نمی‌شود.
تعطیلات رسمی در تقویم نیست؛ در آن روزها هم گزارش ۱۹:۰۰ ساخته می‌شود
و گزارش پیش‌گشایش فقط در روزهای شنبه تا چهارشنبه زمان‌بندی شده است.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from bazaar_intel.config import CLOSE_REPORT_HOUR, CLOSE_REPORT_MINUTE, PREOPEN_HOUR, PREOPEN_MINUTE, TRADING_WEEKDAYS
from bazaar_intel.timeutil import TEHRAN, tehran_now


def next_slot(now: datetime | None = None) -> tuple[datetime, str]:
    moment = (now or tehran_now()).astimezone(TEHRAN)
    candidates: list[tuple[datetime, str]] = []
    for offset in range(0, 8):
        day = (moment + timedelta(days=offset)).replace(second=0, microsecond=0)
        close_at = day.replace(hour=CLOSE_REPORT_HOUR, minute=CLOSE_REPORT_MINUTE)
        if close_at > moment:
            candidates.append((close_at, "close"))
        if day.weekday() in TRADING_WEEKDAYS:
            preopen = day.replace(hour=PREOPEN_HOUR, minute=PREOPEN_MINUTE)
            if preopen > moment:
                candidates.append((preopen, "preopen"))
    if not candidates:
        raise RuntimeError("no schedule slot")
    return min(candidates)


def run_forever(callback) -> None:
    while True:
        when, session = next_slot()
        delay = (when - tehran_now()).total_seconds()
        if delay > 0:
            time.sleep(delay)
        callback(session)
