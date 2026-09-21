from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectPathRequest(BaseModel):
    path: str


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectImportRequest(BaseModel):
    path: str
    name: str | None = Field(default=None, max_length=120)


class ProjectUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectConvertToSourceRequest(BaseModel):
    target_project_id: str = Field(min_length=1, max_length=64)
    source_name: str | None = Field(default=None, max_length=120)


class SourceRequest(BaseModel):
    path: str
    name: str | None = None


class ConversationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200_000)




class ImplementationBriefRequest(BaseModel):
    focus: str = Field(default="", max_length=100_000)

class ProposalRequest(BaseModel):
    request: str = Field(min_length=1, max_length=100_000)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = None


class StagePatchRequest(BaseModel):
    patch_path: str
    repo_path: str


class PlanApprovalRequest(BaseModel):
    approved_actions: list[str] = Field(default_factory=list, max_length=50)


class PatchApprovalRequest(BaseModel):
    approval_checks: list[str] = Field(default_factory=list, max_length=20)
