from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ── GlobalConfig ──

class GlobalConfigCreate(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3"
    system_prompt: str = ""
    notification_method: str = "smtp"
    apprise_url: str = ""


class GlobalConfigRead(GlobalConfigCreate):
    id: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Rule ──

class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    log_file_path: str = Field(..., min_length=1)
    keywords: List[str] = []
    context_lines: int = Field(default=5, ge=0, le=50)
    application_context: str = ""
    enabled: bool = True
    notify_on_match: bool = True
    debounce_seconds: int = Field(default=30, ge=1, le=300)


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    log_file_path: Optional[str] = None
    keywords: Optional[List[str]] = None
    context_lines: Optional[int] = None
    application_context: Optional[str] = None
    enabled: Optional[bool] = None
    notify_on_match: Optional[bool] = None
    debounce_seconds: Optional[int] = None


class RuleRead(RuleCreate):
    id: int
    last_position: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Analysis ──

class AnalysisRead(BaseModel):
    id: int
    rule_id: int
    triggered_line: str
    context_before: List[str] = []
    context_after: List[str] = []
    ollama_response: str
    severity: str
    notified: bool
    analyzed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── File Explorer ──

class FileInfo(BaseModel):
    name: str
    path: str
    size: int
    modified: float
    is_dir: bool
