from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "bazaar.sqlite"
REPORTS_DIR = REPO_ROOT / "reports"
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"

USER_AGENT = "BazaarIntel/0.1 (public-source research; contact local operator)"
HTTP_TIMEOUT = 18

# شنبه تا چهارشنبه. پنجشنبه و جمعه تعطیل هفتگی بازار سهام ایران است.
# تعطیلات رسمی در تقویم ذخیره نشده‌اند و جداگانه اعلام می‌شوند.
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}
PREOPEN_HOUR = 8
PREOPEN_MINUTE = 50
CLOSE_REPORT_HOUR = 19
CLOSE_REPORT_MINUTE = 0
