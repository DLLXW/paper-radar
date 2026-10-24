from pydantic import BaseModel, Field, model_validator

MAX_TOPICS = 12
MAX_KEYWORDS_PER_TOPIC = 10
MAX_TOTAL_KEYWORDS = 60


class TopicIn(BaseModel):
    group: str = ""
    name: str
    description: str = ""
    keywords: list[str] = Field(min_length=0, max_length=MAX_KEYWORDS_PER_TOPIC)


class TopicOut(BaseModel):
    group: str = ""
    name: str
    description: str = ""
    keywords: list[str]


class ProfileOut(BaseModel):
    status: str = "ok"
    categories: list[str]
    topics: list[TopicOut]
    exclude_keywords: list[str]
    daily_limit: int
    updated_at: str


class ProfileIn(BaseModel):
    categories: list[str] = Field(default_factory=list, max_length=20)
    topics: list[TopicIn] = Field(default_factory=list, max_length=MAX_TOPICS)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=50)
    daily_limit: int = Field(ge=5, le=200)

    model_config = {
        "json_schema_extra": {
            "example": {
                "categories": ["cs.LG", "cs.IR", "stat.ML"],
                "topics": [
                    {
                        "group": "用增权益与因果",
                        "name": "DFL/决策一体化",
                        "description": "预测与决策一体化：DFL/DFCL、可微优化、智能出价",
                        "keywords": ["decision-focused learning", "predict-then-optimize"],
                    }
                ],
                "exclude_keywords": ["survey"],
                "daily_limit": 50,
            }
        }
    }

    @model_validator(mode="after")
    def _check_topics(self) -> "ProfileIn":
        total = sum(len(t.keywords) for t in self.topics)
        if total > MAX_TOTAL_KEYWORDS:
            raise ValueError(f"所有 topic 关键词总数不超过 {MAX_TOTAL_KEYWORDS}，当前 {total}")
        names = [t.name.strip() for t in self.topics]
        if any(not n for n in names):
            raise ValueError("topic 名称不能为空")
        if len(names) != len(set(names)):
            raise ValueError("topic 名称不能重复")
        return self
