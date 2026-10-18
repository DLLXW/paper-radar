from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    status: str = "ok"
    categories: list[str]
    keywords: list[str]
    exclude_keywords: list[str]
    daily_limit: int
    updated_at: str


class ProfileIn(BaseModel):
    categories: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(min_length=0, max_length=20)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=50)
    daily_limit: int = Field(ge=5, le=200)

    model_config = {
        "json_schema_extra": {
            "example": {
                "categories": ["cs.CL", "cs.IR"],
                "keywords": ["large language model", "retrieval augmented generation"],
                "exclude_keywords": ["survey"],
                "daily_limit": 30,
            }
        }
    }
