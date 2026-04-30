"""
Router: /api/keyword-learning/*
Exposes keyword auto-learning session management.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import app.services.keyword_learning_service as kls

router = APIRouter(prefix="/api/keyword-learning", tags=["keyword-learning"])


class StartRequest(BaseModel):
    rule_id: Optional[int] = None
    log_file_path: str
    period_start: str   # ISO 8601 local time — caller converts to UTC before sending
    period_end: str     # ISO 8601 local time — same
    granularity_s: int  # seconds per packet


class RevaluateRequest(BaseModel):
    keywords: list[str]


class ValidateRequest(BaseModel):
    keywords: list[str]


def _parse_dt(s: str) -> datetime:
    """Parse ISO datetime string to UTC-naive datetime."""
    s = s.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {s}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.post("/start")
async def start_session(body: StartRequest):
    period_start = _parse_dt(body.period_start)
    period_end   = _parse_dt(body.period_end)
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="invalid_period")
    if body.granularity_s < 60:
        raise HTTPException(status_code=400, detail="invalid_granularity")

    session_id = await kls.start_session(
        rule_id=body.rule_id,
        log_path=body.log_file_path,
        period_start=period_start,
        period_end=period_end,
        granularity_s=body.granularity_s,
    )
    return {"session_id": session_id, "status": "pending"}


@router.get("/{session_id}/status")
def get_status(session_id: int):
    data = kls.get_session_status(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return data


@router.post("/{session_id}/revaluate")
async def revaluate(session_id: int, body: RevaluateRequest):
    data = kls.get_session_status(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    import asyncio
    asyncio.create_task(kls.revaluate_session(session_id, body.keywords))
    return {"status": "refining"}


@router.post("/{session_id}/validate")
async def validate(session_id: int, body: ValidateRequest):
    data = kls.get_session_status(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    await kls.validate_session(session_id, body.keywords)
    return {"status": "validated", "keywords": body.keywords}


@router.post("/{session_id}/revert")
async def revert(session_id: int):
    data = kls.get_session_status(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    prev = await kls.revert_session(session_id)
    return {"status": "reverted", "keywords": prev}


@router.delete("/{session_id}")
def cancel_session(session_id: int):
    """Mark session as cancelled (the background task checks status before each packet)."""
    from app.database import SessionLocal
    from app.models import KeywordLearningSession
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="session_not_found")
        s.status = 'error'
        s.error_message = 'Annulé par l\'utilisateur'
        db.commit()
    finally:
        db.close()
    return {"status": "cancelled"}


@router.get("/{session_id}/log")
def download_session_log(session_id: int):
    """Return the plaintext debug log for a learning session as a download."""
    import app.services.keyword_learning_service as kls_
    log_path = kls_._session_log_path(session_id)
    import os
    if not os.path.isfile(log_path):
        raise HTTPException(
            status_code=404,
            detail="Log de session introuvable (la session n'a peut-être pas encore démarré)."
        )
    filename = f"sentinel_autolearn_session_{session_id}.txt"
    return FileResponse(
        path=log_path,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
