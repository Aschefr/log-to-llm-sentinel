"""
État réel de ce que la page Configuration règle : ce qui a été vérifié, et rien de plus.

Le résultat d'un test ou d'un envoi n'est gardé que pour la configuration qui l'a produit : après une modification,
les notifications redeviennent « non testées » plutôt que de garder un succès qui ne prouve plus rien.
"""
import hashlib
import json
import os
from datetime import datetime
from typing import Optional

from app import logger
from app.database import SessionLocal
from app.models import GlobalConfig
from app.services import dashboard_watch, diagnostics
from app.services.app_config import config_to_dict

_METHOD_FIELDS = {
    "smtp": ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_recipient", "smtp_ssl_mode"),
    "apprise": ("apprise_url", "apprise_tags"),
    "discord": ("discord_webhook_url",),
}


def _method(config: GlobalConfig) -> str:
    method = config.notification_method or "smtp"
    return method if method in _METHOD_FIELDS else "smtp"


def _fingerprint(config: GlobalConfig, method: str) -> str:
    values = [str(getattr(config, field, "") or "") for field in _METHOD_FIELDS[method]]
    return hashlib.sha256("\n".join(values).encode("utf-8", "ignore")).hexdigest()[:16]


def _load(config: GlobalConfig) -> dict:
    try:
        return json.loads(config.notification_state_json or "{}")
    except ValueError:
        return {}


def _configured(config: GlobalConfig, method: str) -> bool:
    if method == "smtp":
        return bool((config.smtp_host or "").strip() and ((config.smtp_recipient or "").strip() or (config.smtp_user or "").strip()))
    if method == "apprise":
        return bool((config.apprise_url or "").strip())
    return bool((config.discord_webhook_url or "").strip())


def _record(kind: str, method: Optional[str], ok: bool):
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            return
        used = method if method in _METHOD_FIELDS else _method(config)
        state = _load(config)
        state.setdefault(kind, {})[used] = {"ok": bool(ok), "at": datetime.utcnow().isoformat() + "Z",
                                            "fingerprint": _fingerprint(config, used)}
        config.notification_state_json = json.dumps(state)
        db.commit()
    except Exception as e:
        logger.error("ConfigStatus", f"Résultat de notification non enregistré : {e}")
    finally:
        db.close()


def record_test(method: str, ok: bool):
    _record("tests", method, ok)


def record_send(method: Optional[str], ok: bool):
    _record("sends", method, ok)


def notifications(config: GlobalConfig) -> dict:
    method = _method(config)
    if not _configured(config, method):
        return {"state": "incomplete", "method": method, "at": None}
    state = _load(config)
    fingerprint = _fingerprint(config, method)
    results = [(kind, state.get(key, {}).get(method)) for kind, key in (("test", "tests"), ("send", "sends"))]
    valid = [(kind, r) for kind, r in results if r and r.get("fingerprint") == fingerprint]
    if not valid:
        return {"state": "untested", "method": method, "at": None}
    kind, latest = max(valid, key=lambda item: item[1]["at"])
    return {"state": "ok" if latest["ok"] else "failed", "kind": kind, "method": method, "at": latest["at"]}


def syslog(config: GlobalConfig) -> dict:
    from app.services.syslog_receiver import detected_hosts, syslog_receiver
    if not config.syslog_enabled:
        return {"state": "off", "hosts": 0, "last_seen": None}
    if syslog_receiver.transport is None:
        return {"state": "not_listening", "hosts": 0, "last_seen": None}
    hosts = detected_hosts()
    return {"state": "receiving" if hosts else "waiting", "hosts": len(hosts), "last_seen": hosts[0]["last_seen"] if hosts else None}


def data_size() -> int:
    """Taille du dossier de données."""
    total = 0
    for dirpath, _, filenames in os.walk(os.environ.get("SENTINEL_DATA_DIR", "/app/data")):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if not os.path.islink(path):
                    total += os.path.getsize(path)
            except OSError:
                pass  # fichier en cours de rotation
    return total


def build(orchestrator=None) -> dict:
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first() or GlobalConfig()
        available = diagnostics.availability()
        return {
            "ollama": dashboard_watch.ollama_status(config_to_dict(config), orchestrator),
            "notifications": notifications(config),
            "syslog": syslog(config),
            "diagnostics": {"docker": bool(available.get("docker")), "host": bool(available.get("host")),
                            "web": bool(available.get("web")), "cve": bool(config.cve_lookup_enabled)},
            "data": {"size_bytes": data_size(), "auto_delete": bool(config.auto_delete_analyses),
                     "retention_days": config.auto_delete_retention_days or 30},
            "advanced": {"debug": bool(config.debug_mode), "log_bytes": logger.log_size()},
        }
    finally:
        db.close()
