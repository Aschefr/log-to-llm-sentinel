from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Rule
import logging

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

def set_orchestrator(orchestrator_instance):
    global _orchestrator
    _orchestrator = orchestrator_instance

@router.post("/logs/{rule_id_or_token}")
async def receive_logs(rule_id_or_token: str, request: Request, db: Session = Depends(get_db)):
    """
    Reçoit des logs d'une machine externe et les injecte directement dans l'orchestrateur.
    Supporte les payloads JSON ({"lines": [...]}) ou le texte brut (une ligne par ligne).
    """
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrateur non configuré")
        
    if rule_id_or_token.isdigit():
        rule = db.query(Rule).filter(Rule.id == int(rule_id_or_token)).first()
    else:
        rule = db.query(Rule).filter(Rule.log_file_path == f"[WEBHOOK]:{rule_id_or_token}").first()
        
    if not rule:
        raise HTTPException(status_code=404, detail="Règle non trouvée")
        
    if not rule.enabled:
        raise HTTPException(status_code=400, detail="Règle désactivée")

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
            raise HTTPException(status_code=400, detail="JSON invalide")
    else:
        body_bytes = await request.body()
        text = body_bytes.decode('utf-8', errors='ignore')
        lines = [line.strip() for line in text.splitlines() if line.strip()]

    if not lines:
        return {"status": "ok", "message": "Aucune ligne à traiter"}

    await _orchestrator.handle_new_lines(rule, lines)
    
    return {"status": "ok", "lines_received": len(lines)}
