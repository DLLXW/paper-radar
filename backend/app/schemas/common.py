from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str                    # ok | degraded
    app: str
    version: str
    db: str                        # ok | error
    llm_configured: bool
    reader_configured: bool
    push_channels: list[str]
    time: str
