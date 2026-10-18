"""从 arXiv comment（作者投稿备注）中提取会议/期刊信息。

arXiv Atom API 的 <arxiv:comment> 常见形态：
  "Accepted at NeurIPS 2026" / "ICML 2026 poster" / "Camera-ready for KDD '26"
确定性正则提取，不做任何外部调用（防坑：抓取循环内零富化）。
"""

import re

# 名称归一化（NIPS 旧称 → NeurIPS；SIGKDD → KDD）
_VENUES = [
    "NeurIPS", "NIPS", "ICML", "ICLR", "AISTATS", "COLT", "UAI", "AAAI", "IJCAI",
    "KDD", "SIGKDD", "WWW", "WSDM", "RecSys", "SIGIR", "CIKM", "AAMAS", "EC",
    "CVPR", "ICCV", "ECCV", "ACL", "EMNLP", "NAACL", "COLING",
    "JMLR", "TMLR", "Nature", "Science", "PNAS",
]
_NORMALIZE = {"NIPS": "NeurIPS", "SIGKDD": "KDD"}
_ALTERNATIVES = "|".join(sorted(_VENUES, key=len, reverse=True))

# 形态一：NeurIPS 2026 / KDD '26 / ICML26
_VENUE_YEAR = re.compile(rf"\b({_ALTERNATIVES})\s*['`]?\s*(20\d{{2}}|\d{{2}})\b")
# 形态二：2026 NeurIPS
_YEAR_VENUE = re.compile(rf"\b(20\d{{2}})\s+({_ALTERNATIVES})\b")
# 形态三：裸会议名 + 上下文有接收类词
_BARE_VENUE = re.compile(rf"\b({_ALTERNATIVES})\b", re.IGNORECASE)
_ACCEPT_WORDS = re.compile(
    r"accept|appear|publish|camera[- ]ready|oral|spotlight|poster|submit|under review",
    re.IGNORECASE,
)


def _fix_year(year: str) -> str | None:
    year = year.strip("'`")
    if len(year) == 2:
        return f"20{year}"
    return year if len(year) == 4 else None


def extract_venue(comment: str | None) -> str | None:
    """返回如 "NeurIPS 2026"（已接收）/ "ICLR 2027（投稿）"；提取不到返回 None。"""
    if not comment:
        return None
    text = comment.strip()

    name = year = None
    m = _VENUE_YEAR.search(text)
    if m:
        name, year = m.group(1), _fix_year(m.group(2))
    else:
        m = _YEAR_VENUE.search(text)
        if m:
            name, year = m.group(2), m.group(1)
        elif _ACCEPT_WORDS.search(text):
            bare = _BARE_VENUE.search(text)
            if bare:
                name = bare.group(1)
    if not name:
        return None

    name = _NORMALIZE.get(name, name)
    label = f"{name} {year}" if year else name

    # 上下文判断是「已接收」还是「投稿中」
    if m:
        context = text[max(0, m.start() - 45) : m.end() + 15].lower()
        if re.search(r"submi|under review|preprint", context):
            return f"{label}（投稿）"
    return label
