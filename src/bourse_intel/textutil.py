from __future__ import annotations

import re
import unicodedata

_YE = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "\u200c": " ", "\u200d": ""})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_YE)
    text = text.replace("٪", " درصد ")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s\u0600-\u06FF]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u0600-\u06FF]{2,}", normalize(text)))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def strip_html(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"[ \t]+", " ", text).strip()
