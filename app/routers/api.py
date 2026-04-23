from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Rule, Analysis

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/analyze")
def manual_analyze(rule_id: int, db: Session = Depends(get_db)):
    """Déclenche manuellement une analyse pour une règle donnée.
    (L'orchestration complète sera implémentée à l'étape 5.)"""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Règle non trouvée")

    # Placeholder — l'implémentation complète arrive plus tard
    return {"detail": f"Analyse déclenchée pour la règle {rule.name}", "rule_id": rule_id}
