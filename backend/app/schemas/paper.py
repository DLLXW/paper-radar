from pydantic import BaseModel, Field


class PaperOut(BaseModel):
    id: str
    version: str
    kind: str
    source: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    primary_category: str | None
    arxiv_url: str
    pdf_url: str | None
    published_at: str
    first_seen_at: str
    fetched_date: str
    score: float | None
    reason: str | None
    summary: str | None
    topic: str | None
    matched_keywords: list[str]
    status: str
    read_doc_name: str | None
    comment: str | None
    venue: str | None


class PapersResponse(BaseModel):
    status: str = "ok"
    date: str
    total: int
    papers: list[PaperOut]
    has_more: bool
    next_start: int | None


class RunSummary(BaseModel):
    fetched: int
    scored: int
    alerted: int


class PapersStatusResponse(BaseModel):
    status: str = "ok"
    running: bool
    run_id: int | None
    last_run_at: str | None
    last_run_summary: RunSummary | None
    last_run_status: str | None
    next_run_at: str | None
    papers_today: int


class PaperStatusPatch(BaseModel):
    status: str = Field(pattern="^(new|starred|dismissed)$")


class FetchStartedResponse(BaseModel):
    status: str
    run_id: int | None = None
    message: str | None = None
