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
    keywords = [k.strip() for k in payload.keywords if k.strip()]
    excludes = [e.strip() for e in payload.exclude_keywords if e.strip()]
    saved = store.save_profile(categories, keywords, excludes, payload.daily_limit)
    return ProfileOut(status="ok", **saved)
