"""
Router: /api/keyword-learning/*
Sessions d'apprentissage des mots-clés d'une règle (analyse d'une période passée ou observation).
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import app.services.keyword_learning_service as kls

router = APIRouter(prefix="/api/keyword-learning", tags=["keyword-learning"])


class StartRequest(BaseModel):
    rule_id: int
    mode: str = "history"   # 'history' | 'live'
    hours: int = 168


@router.post("/start")
async def start_session(body: StartRequest):
    try:
        session_id = kls.start_session(body.rule_id, body.mode, body.hours)
    except ValueError as e:
        raise HTTPException(status_code=404 if str(e) == "rule_not_found" else 422, detail=str(e))
    return {"session_id": session_id}


@router.get("/{session_id}/status")
def get_status(session_id: int):
    data = kls.get_session_status(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return data


@router.post("/{session_id}/revert")
def revert(session_id: int):
    data = kls.revert_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return {"status": "reverted", **data}


@router.delete("/{session_id}")
def cancel_session(session_id: int):
    if not kls.cancel_session(session_id):
        raise HTTPException(status_code=404, detail="session_not_found")
    return {"status": "cancelled"}


@router.get("/{session_id}/log")
def download_session_log(session_id: int):
    """Données envoyées à l'IA et décision rendue, en texte."""
    text = kls.session_exchange_text(session_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Aucun échange avec l'IA pour cette session.")
    return PlainTextResponse(text, headers={
        "Content-Disposition": f'attachment; filename="sentinel_keywords_session_{session_id}.txt"'})
