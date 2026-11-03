"""
Interrupteur général de la surveillance.

En pause, rien d'automatique ne sollicite l'IA ni ne notifie : ni analyse d'alerte, ni contrôle de résolution, ni revue
des mots-clés, ni méta-analyse planifiée, ni avertissement de silence. Les sources continuent d'être lues, sans rien
analyser : les lignes reçues entre-temps restent dans les logs, mais ne déclenchent pas une avalanche à la reprise.
Ce que l'utilisateur lance lui-même (chat, méta-analyse manuelle) reste possible.

L'état est lu en base à chaque fois : il survit aux redémarrages et il n'y a pas de copie à garder à jour.
"""
from datetime import datetime
from typing import Optional

from app.models import GlobalConfig
from app.services.rule_stats import iso

EXCLUDED_FROM_BACKUP = ("monitoring_paused", "monitoring_paused_at")


def is_paused(db) -> bool:
    row = db.query(GlobalConfig.monitoring_paused).first()
    return bool(row and row[0])


def state(db) -> dict:
    row = db.query(GlobalConfig.monitoring_paused, GlobalConfig.monitoring_paused_at).first()
    paused = bool(row and row[0])
    return {"paused": paused, "since": iso(row[1]) if paused else None}


def set_paused(db, paused: bool, resolution_service=None, now: Optional[datetime] = None) -> dict:
    """À la reprise, les incidents ouverts retrouvent leur délai de calme là où il en était : personne n'a regardé
    les lignes pendant la pause, ce silence ne prouve pas que le problème est réglé."""
    now = now or datetime.utcnow()
    config = db.query(GlobalConfig).first()
    if not config:
        config = GlobalConfig()
        db.add(config)
    if paused and not config.monitoring_paused:
        config.monitoring_paused = True
        config.monitoring_paused_at = now
    elif not paused and config.monitoring_paused:
        since = config.monitoring_paused_at
        config.monitoring_paused = False
        config.monitoring_paused_at = None
        if resolution_service and since:
            resolution_service.shift_timers(now - since)
    db.commit()
    return state(db)
