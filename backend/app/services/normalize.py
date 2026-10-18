"""论文输入归一化：接受 arXiv abs/pdf 链接、HF papers 链接、裸 id（含版本号）。"""

import re
from urllib.parse import urlparse

# 新式 2401.12345 / 旧式 cs/0301012 / math.CO/0709123
_ARXIV_ID = r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)"

_PATTERNS = [
    re.compile(rf"^https?://arxiv\.org/abs/{_ARXIV_ID}/?$", re.IGNORECASE),
    re.compile(rf"^https?://arxiv\.org/pdf/{_ARXIV_ID}(?:\.pdf)?/?$", re.IGNORECASE),
    re.compile(rf"^https?://huggingface\.co/papers/{_ARXIV_ID}/?$", re.IGNORECASE),
    re.compile(rf"^{_ARXIV_ID}/?$"),
]


class InvalidPaperInput(ValueError):
    pass


def normalize_paper_input(raw: str) -> dict:
    """归一化为 {bare_id, version, arxiv_url, pdf_url}；无法识别抛 InvalidPaperInput。"""
    text = (raw or "").strip()
    if not text:
        raise InvalidPaperInput("输入为空")

    patterns = _PATTERNS[:3] if urlparse(text).scheme in ("http", "https") else _PATTERNS[3:]
    matched = None
    for pattern in patterns:
        m = pattern.match(text)
        if m:
            matched = m
            break
    if matched is None:
        if urlparse(text).scheme in ("http", "https"):
            raise InvalidPaperInput(
                f"不支持的链接：{text}（支持 arxiv.org/abs、arxiv.org/pdf、huggingface.co/papers）"
            )
        raise InvalidPaperInput(f"无法识别的论文 id：{text}")

    full_id = matched.group(1)
    ver = re.search(r"v(\d+)$", full_id)
    bare_id = full_id[: ver.start()] if ver else full_id
    version = f"v{ver.group(1)}" if ver else "v1"
    return {
        "bare_id": bare_id,
        "version": version,
        "arxiv_url": f"https://arxiv.org/abs/{bare_id}",
        "pdf_url": f"https://arxiv.org/pdf/{bare_id}",
    }
