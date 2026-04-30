from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Rule
from collections import deque
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import logging
import os

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

router = APIRouter(prefix="/api/webhook", tags=["webhook"])
logger = logging.getLogger(__name__)

# Instance globale de l'orchestrateur (injectée au démarrage)
_orchestrator = None

# Ring buffer per rule token — max 500 lines kept in memory
_BUFFER_MAX = 500
_webhook_buffers: dict[str, deque] = {}
_WEBHOOK_LOG_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "webhooks"

def set_orchestrator(orchestrator_instance):
    global _orchestrator
    _orchestrator = orchestrator_instance

def _log_path(token: str) -> Path:
    """Safe file path for a webhook token log."""
    safe = "".join(c for c in token if c.isalnum() or c in "-_")
    return _WEBHOOK_LOG_DIR / f"{safe}.log"

def _get_buffer(token: str) -> deque:
    """Get or create buffer, pre-loading from disk on first access."""
    if token not in _webhook_buffers:
        buf = deque(maxlen=_BUFFER_MAX)
        # Load existing lines from disk
        fp = _log_path(token)
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        buf.append(line.rstrip("\n"))
            except Exception:
                pass
        _webhook_buffers[token] = buf
    return _webhook_buffers[token]

def _append_to_disk(token: str, lines: list[str]):
    """Append lines to persistent log file."""
    fp = _log_path(token)
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        # Truncate if file grows beyond 2x buffer max
        _maybe_truncate(fp)
    except Exception as e:
        logger.warning(f"Webhook log write error: {e}")

def _maybe_truncate(fp: Path):
    """Keep only last _BUFFER_MAX lines on disk to cap file size."""
    try:
        stat = fp.stat()
        # Only check when file exceeds ~100KB
        if stat.st_size < 100_000:
            return
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        if len(all_lines) > _BUFFER_MAX:
            with open(fp, "w", encoding="utf-8") as f:
                f.writelines(all_lines[-_BUFFER_MAX:])
    except Exception:
        pass

@router.post("/logs/{rule_id_or_token}")
async def receive_logs(rule_id_or_token: str, request: Request, db: Session = Depends(get_db)):
    """
    Reçoit des logs d'une machine externe et les injecte directement dans l'orchestrateur.
    Supporte les payloads JSON ({"lines": [...]}) ou le texte brut (une ligne par ligne).
    """
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="orchestrator_not_configured")
        
    if rule_id_or_token.isdigit():
        rule = db.query(Rule).filter(Rule.id == int(rule_id_or_token)).first()
    else:
        rule = db.query(Rule).filter(Rule.log_file_path == f"[WEBHOOK]:{rule_id_or_token}").first()
        
    if not rule:
        raise HTTPException(status_code=404, detail="rule_not_found")
        
    if not rule.enabled:
        raise HTTPException(status_code=400, detail="rule_disabled")

    content_type = request.headers.get("content-type", "")
    lines = []
    
    if "application/json" in content_type:
        try:
            data = await request.json()
            if isinstance(data, dict) and "lines" in data:
                lines = data["lines"]
            elif isinstance(data, list):
                lines = data
            else:
                lines = [str(data)]
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_json")
    else:
        body_bytes = await request.body()
        text = body_bytes.decode('utf-8', errors='ignore')
        lines = [line.strip() for line in text.splitlines() if line.strip()]

    if not lines:
        return {"status": "ok", "message": "Aucune ligne à traiter"}

    # Store in ring buffer + persist to disk
    token = rule.log_file_path.split(":", 1)[1] if ":" in rule.log_file_path else str(rule.id)
    buf = _get_buffer(token)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stamped = [f"{ts}  {line}" for line in lines]
    for s in stamped:
        buf.append(s)
    _append_to_disk(token, stamped)

    await _orchestrator.handle_new_lines(rule, lines)
    
    return {"status": "ok", "lines_received": len(lines)}


@router.get("/tail/{token}")
def tail_webhook(
    token: str,
    lines: int = Query(60, description="Nombre de lignes"),
    keywords: Optional[str] = Query(None, description="Mots-clés pour colorisation")
):
    """Retourne les dernières lignes reçues par webhook (ring buffer mémoire, persisté sur disque)."""
    buf = _get_buffer(token)  # Will load from disk if first access
    
    kw_list = [kw.strip().lower() for kw in keywords.split(",") if kw.strip()] if keywords else []
    
    tail = list(buf)[-lines:]
    result = []
    for raw in tail:
        matched_kws = [kw for kw in kw_list if kw in raw.lower()] if kw_list else []
        result.append({
            "text": raw,
            "matched": len(matched_kws) > 0,
            "matched_keywords": matched_kws
        })
    return {"lines": result}
