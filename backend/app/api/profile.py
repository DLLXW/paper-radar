import re

from fastapi import APIRouter, HTTPException

from app.db import store
from app.schemas.profile import ProfileIn, ProfileOut

router = APIRouter(tags=["profile"])

_CATEGORY_RE = re.compile(r"^[a-z-]+(\.[A-Z]{2})?$")


def _validate_category(value: str) -> str:
    text = value.strip()
    if not _CATEGORY_RE.match(text):
        raise ValueError(f"分类格式应为 cs.CL / math.CO 形式，收到: {text}")
    return text


@router.get("/profile", response_model=ProfileOut)
async def get_profile() -> ProfileOut:
    return ProfileOut(status="ok", **store.get_profile())


@router.put("/profile", response_model=ProfileOut)
async def put_profile(payload: ProfileIn) -> ProfileOut:
    try:
        categories = [_validate_category(c) for c in payload.categories if c.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    topics: list[dict] = []
    seen_names: set[str] = set()
    for t in payload.topics:
        name = t.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="topic 名称不能为空")
        if name in seen_names:
            raise HTTPException(status_code=422, detail=f"topic 名称重复: {name}")
        seen_names.add(name)
        topics.append(
            {
                "group": t.group.strip(),
                "name": name,
                "description": t.description.strip(),
                "keywords": [k.strip() for k in t.keywords if k.strip()],
            }
        )
    if not topics:
        raise HTTPException(status_code=422, detail="至少需要一个 topic（含至少一个关键词更利于抓取）")
    excludes = [e.strip() for e in payload.exclude_keywords if e.strip()]
    saved = store.save_profile(categories, topics, excludes, payload.daily_limit)
    return ProfileOut(status="ok", **saved)
