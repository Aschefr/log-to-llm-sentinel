from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import SessionLocal
from app.services import keyword_learning_service as kls, unread_triage

router = APIRouter(prefix="/api/triage", tags=["triage"])


class ApplyRequest(BaseModel):
    job_id: str
    keys: List[dict]


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "started_at"}


@router.post("/start")
async def start_triage(rule_id: Optional[int] = None):
    if not kls._tuning:
        raise HTTPException(status_code=503, detail="triage_unavailable")
    return _public(unread_triage.start(rule_id, kls._tuning))


@router.get("/jobs/{job_id}")
def get_triage(job_id: str):
    job = unread_triage.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="triage_expired")
    return _public(job)


@router.delete("/jobs/{job_id}")
def cancel_triage(job_id: str):
    return {"cancelled": unread_triage.cancel(job_id)}


@router.post("/apply")
def apply_triage(body: ApplyRequest):
    db = SessionLocal()
    try:
        return unread_triage.apply(db, body.job_id, body.keys)
    except LookupError:
        raise HTTPException(status_code=409, detail="triage_expired")
    finally:
        db.close()


@router.get("/last")
def last_triage():
    db = SessionLocal()
    try:
        return {"last": unread_triage.last(db)}
    finally:
        db.close()


@router.post("/undo")
def undo_triage():
    db = SessionLocal()
    try:
        return unread_triage.undo(db)
    finally:
        db.close()
