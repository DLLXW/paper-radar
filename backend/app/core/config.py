from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "PaperRadar"
    app_version: str = "0.1.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8300

    # LLM 打分（唯一必填项；缺省时服务照常启动，仅跳过打分）
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # 抓取与保留
    fetch_hour: int = 8
    paper_retention_days: int = 90

    # 推送：飞书
    feishu_webhook_url: str | None = None

    # 推送：邮件 SMTP（587 STARTTLS）
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None

    # 精读服务（可选插件）
    reader_base_url: str | None = None

    # 存储/静态资源
    data_dir: Path = Field(default=BACKEND_DIR / "data")
    frontend_dist_dir: Path = Field(default=BACKEND_DIR.parent / "frontend" / "dist")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RADAR_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
