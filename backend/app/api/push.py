from fastapi import APIRouter

from app.services.push import push_test

router = APIRouter(tags=["push"])


@router.post("/push/test")
async def test_push() -> dict:
    """推送配置自检：向所有已配置渠道发一条测试消息并汇报结果。"""
    results = await push_test()
    channels = {
        key: value
        for key, value in results.items()
        if key != "_configured" and isinstance(value, dict)
    }
    overall = (
        "ok" if all(r.get("status") in ("ok", "skipped") for r in channels.values()) else "error"
    )
    return {"status": overall, "channels": results}
