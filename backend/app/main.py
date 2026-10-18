import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.api.digests import router as digests_router
from app.api.papers import router as papers_router
from app.api.profile import router as profile_router
from app.api.push import router as push_router
from app.api.system import router as system_router
from app.core.config import get_settings
from app.db.store import init_db
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("paperradar")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await start_scheduler()
    logger.info("%s v%s 就绪（数据目录 %s）", settings.app_name, settings.app_version, settings.data_dir)
    yield
    await stop_scheduler()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(system_router, prefix="/api")
app.include_router(papers_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(push_router, prefix="/api")
app.include_router(digests_router, prefix="/api")


class SPAStaticFiles(StaticFiles):
    """静态托管 frontend/dist，未命中路径回退 index.html（SPA fallback）。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


if settings.frontend_dist_dir.exists():
    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist_dir, html=True), name="spa")
else:

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "hint": "frontend/dist 不存在：请先在 frontend/ 下执行 npm run build",
            "health": "/api/health",
        }
