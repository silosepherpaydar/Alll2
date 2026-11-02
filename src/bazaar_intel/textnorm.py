"""نرمال‌سازی فارسی و تشخیص بازنشر."""

from __future__ import annotations

import hashlib
import re
import unicodedata

YEHS = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ؤ": "و", "أ": "ا", "إ": "ا"})
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
TOKEN_RE = re.compile(r"[A-Za-z0-9\u0600-\u06FF]+")
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_\u0600-\u06FF]{2,})")

INDUSTRY_KEYWORDS = (
    ("پتروشیمی", "پتروشیمی"),
    ("پالایش", "پالایش"),
    ("نفت", "نفت و گاز"),
    ("گاز", "نفت و گاز"),
    ("فولاد", "فلزات اساسی"),
    ("مس ", "فلزات اساسی"),
    ("آلومینیوم", "فلزات اساسی"),
    ("سیمان", "سیمان"),
    ("دارو", "دارویی"),
    ("غذا", "غذایی"),
    ("لبنی", "غذایی"),
    ("بانک", "بانکی"),
    ("بیمه", "بیمه"),
    ("خودرو", "خودرو"),
    ("حمل و نقل", "حمل‌ونقل"),
    ("حمل‌ونقل", "حمل‌ونقل"),
    ("کشتیرانی", "حمل‌ونقل"),
    ("مخابرات", "مخابرات"),
    ("همراه", "مخابرات"),
    ("نرم افزار", "فناوری اطلاعات"),
    ("نرم‌افزار", "فناوری اطلاعات"),
    ("برق", "نیروگاه و برق"),
    ("نیروگاه", "نیروگاه و برق"),
    ("کشاورز", "زراعت"),
    ("زراعت", "زراعت"),
    ("ساختمان", "ساختمانی"),
    ("لیزینگ", "لیزینگ"),
    ("سرمایه گذاری", "سرمایه‌گذاری"),
    ("سرمایه‌گذاری", "سرمایه‌گذاری"),
    ("طلا", "طلا و کالا"),
    ("سکه", "طلا و کالا"),
    ("شمش", "طلا و کالا"),
    ("اوره", "محصولات شیمیایی"),
    ("متانول", "محصولات شیمیایی"),
    ("خوراک", "پتروشیمی"),
    ("تعرفه", "سیاست تجاری"),
    ("تحریم", "سیاست تجاری"),
    ("نرخ ارز", "ارز"),
    ("دلار", "ارز"),
    ("افزایش سرمایه", "اقدام شرکتی"),
    ("تجدید ارزیابی", "اقدام شرکتی"),
    ("مجمع", "اقدام شرکتی"),
)

MARKET_HINTS = (
    "بورس",
    "فرابورس",
    "سهام",
    "نماد",
    "کدال",
    "شاخص",
    "صندوق",
    "اختیار",
    "آتی",
    "اوراق",
    "طلا",
    "دلار",
    "ارز",
    "نفت",
    "پتروشیمی",
    "پالایشگاه",
    "پالایشی",
    "فولاد",
    "سیمان",
    "بانک",
    "بیمه",
    "مجمع",
    "افزایش سرمایه",
    "تعرفه",
    "خوراک",
    "تحریم",
    "شمش",
    "سکه",
    "گواهی سپرده",
    "شفاف سازی",
    "عرضه اولیه",
    "ایران خودرو",
    "سایپا",
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(YEHS).translate(DIGITS)
    text = text.replace("\u200c", " ").replace("\xa0", " ")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def tokens(text: str) -> set[str]:
    return {tok for tok in TOKEN_RE.findall(normalize(text)) if len(tok) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def fingerprint(text: str) -> str:
    toks = sorted(tokens(text))
    digest = hashlib.sha256(" ".join(toks).encode("utf-8")).hexdigest()
    return digest[:16]


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def hashtags(text: str) -> list[str]:
    seen: list[str] = []
    for tag in HASHTAG_RE.findall(text or ""):
        cleaned = tag.translate(YEHS).translate(DIGITS).replace("_", " ").strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def contains_term(text: str, needle: str) -> bool:
    hay = normalize(text)
    term = normalize(needle)
    if not term:
        return False
    start = 0
    while True:
        index = hay.find(term, start)
        if index < 0:
            return False
        before = hay[index - 1] if index else " "
        after_index = index + len(term)
        after = hay[after_index] if after_index < len(hay) else " "
        if not _is_word_char(before) and not _is_word_char(after):
            return True
        start = index + 1


def _is_word_char(char: str) -> bool:
    return char.isalnum() or "\u0600" <= char <= "\u06FF"


def industry_tags(text: str) -> list[str]:
    found: list[str] = []
    for needle, label in INDUSTRY_KEYWORDS:
        if contains_term(text, needle) and label not in found:
            found.append(label)
    return found


def looks_market_relevant(text: str) -> bool:
    return any(contains_term(text, hint) for hint in MARKET_HINTS)


def effective_verification(stored: str, text: str) -> str:
    if stored in {"official_confirmed", "reputable_media"}:
        return stored
    if "اطلاعیه" in text or "کدال" in text:
        return stored
    if any(token in text for token in ("شایعه", "شنیده", "هک شدن", "هک شده")) or "؟" in text:
        return "rumor"
    return stored


_UNIT_WORDS = (
    ("هزار میلیارد", 10**12),
    ("میلیارد", 10**9),
    ("میلیون", 10**6),
    ("هزار", 10**3),
)


def parse_decimal(text: str | None) -> str | None:
    """عدد ساده را به رشته اعشاری برمی‌گرداند. واحدهای متنی را اینجا تفسیر نمی‌کند."""
    if text is None:
        return None
    raw = text.translate(DIGITS).strip().replace("٬", "").replace(",", "").replace(" ", "")
    raw = raw.replace("٫", ".")
    if not raw or raw in {"-", "—", "None"}:
        return None
    if not re.fullmatch(r"-?\d+(\.\d+)?", raw):
        return None
    if "." in raw:
        whole, frac = raw.split(".", 1)
        raw = f"{int(whole)}.{frac}"
    else:
        raw = str(int(raw))
    return raw


def parse_scaled(text: str | None) -> tuple[str | None, str | None]:
    """عدد با واحد فارسی. خروجی (مقدار, متن خام). اگر واحد ناشناس باشد مقدار None است."""
    if text is None:
        return None, None
    raw = " ".join(text.translate(DIGITS).replace("٬", "").split())
    if raw in {"", "-", "—"}:
        return None, raw
    plain = parse_decimal(raw)
    if plain is not None:
        return plain, raw
    for word, factor in _UNIT_WORDS:
        if word in raw:
            number = raw.replace(word, "").strip()
            base = parse_decimal(number)
            if base is None:
                return None, raw
            from decimal import Decimal

            value = Decimal(base) * Decimal(factor)
            rendered = format(value, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return rendered, raw
    return None, raw
