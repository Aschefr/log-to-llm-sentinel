from fastapi import APIRouter, HTTPException, Request, Query
from app.database import SessionLocal
from app.models import GlobalConfig, Rule
from app import logger
from collections import deque
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import asyncio
import json
import os
import re

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

# Instance globale de l'orchestrateur (injectée au démarrage)
_orchestrator = None

# Ring buffer per rule token — max 500 lines kept in memory
_BUFFER_MAX = 2000
_webhook_buffers: dict[str, deque] = {}
_WEBHOOK_LOG_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "webhooks"
MAX_LINE_CHARS = 10_000
# Un gabarit qui colle le message dans le JSON sans l'échapper : un guillemet ou une trace d'exception le rend invalide,
# et ce sont justement les erreurs les plus utiles.
_UNESCAPED_LINES = re.compile(r'^\s*\{\s*"lines"\s*:\s*\[\s*"(.*)"\s*\]\s*\}\s*$', re.DOTALL)

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
        
        limit_mb = 10
        db = SessionLocal()
        try:
            config = db.query(GlobalConfig).first()
            if config and config.log_rotation_limit_mb:
                limit_mb = config.log_rotation_limit_mb
        except Exception:
            pass
        finally:
            db.close()
            
        from app.utils.log_utils import rotate_log_file
        rotate_log_file(fp, limit_mb * 1024 * 1024)
    except Exception as e:
        logger.warning("Webhook", f"Écriture du journal {fp.name} : {e}")


def _as_text(item) -> str:
    return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)


def parse_lines(body: bytes, content_type: str) -> list[str]:
    """Les lignes d'un envoi, quel qu'en soit le format. Un log n'est jamais refusé pour sa forme : ce qui n'est pas du
    JSON valide est lu comme du texte."""
    text = body.decode("utf-8", errors="replace")
    items = [text]
    if "application/json" in content_type:
        try:
            data = json.loads(text)
            data = data["lines"] if isinstance(data, dict) and "lines" in data else data
            items = data if isinstance(data, list) else [data]
        except ValueError:
            salvaged = _UNESCAPED_LINES.match(text)
            items = [salvaged.group(1) if salvaged else text]
    lines = []
    for item in items:
        for line in _as_text(item).splitlines():
            line = line.strip()
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + f" …[TRONQUÉ : {len(line)} chars]"
            if line:
                lines.append(line)
    return lines


def _find_rule(rule_id_or_token: str) -> Optional[Rule]:
    """La connexion à la base est rendue tout de suite : gardée pendant la suite de l'envoi, une rafale les épuiserait
    toutes et chaque requête attendrait les autres."""
    db = SessionLocal()
    try:
        if rule_id_or_token.isdigit():
            return db.query(Rule).filter(Rule.id == int(rule_id_or_token)).first()
        return db.query(Rule).filter(Rule.log_file_path == f"[WEBHOOK]:{rule_id_or_token}").first()
    finally:
        db.close()


def _mark_received(rule_id: int):
    db = SessionLocal()
    try:
        db.query(Rule).filter(Rule.id == rule_id).update(
            {Rule.last_line_received_at: datetime.utcnow(), Rule.inactivity_notified: False}, synchronize_session=False)
        db.commit()
    finally:
        db.close()

@router.post("/logs/{rule_id_or_token}")
async def receive_logs(rule_id_or_token: str, request: Request):
    """
    Reçoit des logs d'une machine externe et les injecte directement dans l'orchestrateur.
    Supporte les payloads JSON ({"lines": [...]}) ou le texte brut (une ligne par ligne).
    """
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="orchestrator_not_configured")
        
    rule = _find_rule(rule_id_or_token)
    if not rule:
        raise HTTPException(status_code=404, detail="rule_not_found")
        
    if not rule.enabled:
        raise HTTPException(status_code=400, detail="rule_disabled")

    lines = parse_lines(await request.body(), request.headers.get("content-type", ""))

    if not lines:
        return {"status": "ok", "message": "Aucune ligne à traiter"}

    # Store in ring buffer + persist to disk
    token = rule.log_file_path.split(":", 1)[1] if ":" in rule.log_file_path else str(rule.id)
    buf = _get_buffer(token)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stamped = [f"{ts}  {line}" for line in lines]
    for s in stamped:
        buf.append(s)
    await asyncio.to_thread(_append_to_disk, token, stamped)

    # Mettre à jour la date de dernière ligne reçue et réinitialiser le statut d'inactivité (MON-14 & MON-15)
    # Hors de la boucle : une base occupée ne doit retarder ni l'émetteur, ni les autres sources
    try:
        await asyncio.to_thread(_mark_received, rule.id)
    except Exception as e:
        logger.warning("Webhook", f"Date de dernière ligne de la règle {rule.id} : {e}")

    # Les lignes sont reçues : une erreur de traitement ne doit pas les faire passer pour refusées
    try:
        await _orchestrator.handle_new_lines(rule, lines)
    except Exception as e:
        logger.error("Webhook", f"Traitement de {len(lines)} ligne(s) de la règle {rule.id} : {e}")

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
