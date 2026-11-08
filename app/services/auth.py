"""Connexion facultative à l'interface : clés d'accès (WebAuthn) et mot de passe de secours.

Désactivée par défaut : une installation existante reste ouverte comme avant. Une fois activée, tout passe par une session
(cookie HttpOnly, SameSite=Lax), sauf la page de connexion, les fichiers statiques et la réception des webhooks
(voir app/auth_gate.py). L'état est gardé en mémoire pour que la vérification de chaque requête ne touche pas la base :
la boucle principale ne doit jamais attendre SQLite.

Récupération : un fichier data/reset-auth présent au démarrage coupe la connexion et efface clés, mot de passe et sessions.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from app import logger
from app.database import SessionLocal
from app.models import AuthPasskey, AuthSession, AuthState

COOKIE_NAME = "sentinel_session"
SESSION_DAYS = 30
SESSIONS_MAX = 50
TOUCH_EVERY = timedelta(hours=1)  # la date de dernier usage est écrite au plus une fois par heure
PASSWORD_MIN = 12
PASSWORD_MAX = 1024
RESET_FILE = "reset-auth"
CHALLENGE_SECONDS = 300

# scrypt : environ 64 Mo et quelques dizaines de millisecondes par essai
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_lock = threading.Lock()
_enabled = False
_sessions: dict = {}  # hachage du jeton -> {"id", "expires_at", "last_seen_at"}
_challenges: dict = {}  # identifiant -> {"challenge", "rp_id", "origin", "purpose", "expires"}


class AuthError(Exception):
    """Refus expliqué à l'interface : `code` est une clé de traduction api_errors.<code>."""

    def __init__(self, code: str, status: int = 400, **extra):
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def _data_dir() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data"))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── État ────────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return _enabled


def _state(db) -> AuthState:
    state = db.query(AuthState).first()
    if not state:
        state = AuthState(enabled=False)
        db.add(state)
        db.flush()
    return state


def load():
    """Au démarrage, après init_db : applique data/reset-auth s'il existe, puis met l'état et les sessions en mémoire."""
    global _enabled
    apply_reset_file()
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.query(AuthSession).filter(AuthSession.expires_at < now).delete(synchronize_session=False)
        state = _state(db)
        db.commit()
        with _lock:
            _enabled = bool(state.enabled)
            _sessions.clear()
            for s in db.query(AuthSession).all():
                _sessions[s.token_hash] = {"id": s.id, "expires_at": s.expires_at, "last_seen_at": s.last_seen_at}
    finally:
        db.close()
    if _enabled:
        logger.info("Auth", "Connexion à l'interface active")


def apply_reset_file() -> bool:
    """data/reset-auth : celui qui a la main sur le dossier data/ a déjà tout le reste. Le fichier est supprimé une fois
    appliqué, pour qu'un redémarrage suivant ne recommence pas."""
    path = _data_dir() / RESET_FILE
    if not path.exists():
        return False
    db = SessionLocal()
    try:
        db.query(AuthSession).delete(synchronize_session=False)
        db.query(AuthPasskey).delete(synchronize_session=False)
        state = _state(db)
        state.enabled = False
        state.password_hash = None
        state.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    try:
        path.unlink()
    except OSError as e:
        logger.warning("Auth", f"Le fichier {RESET_FILE} n'a pas pu être supprimé, la réinitialisation recommencera au prochain démarrage : {e}")
    logger.warning("Auth", f"Fichier {RESET_FILE} trouvé : connexion à l'interface désactivée, clés d'accès, mot de passe et sessions effacés")
    return True


def status(db, current_session_id: Optional[int]) -> dict:
    state = _state(db)
    passkeys = db.query(AuthPasskey).order_by(AuthPasskey.created_at).all()
    now = datetime.utcnow()
    sessions = db.query(AuthSession).filter(AuthSession.expires_at >= now).count()
    return {
        "enabled": bool(state.enabled),
        "password_set": bool(state.password_hash),
        "passkeys": [{"id": p.id, "name": p.name, "rp_id": p.rp_id,
                      "created_at": p.created_at.isoformat() if p.created_at else None,
                      "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None} for p in passkeys],
        "sessions": sessions,
        "current_session": current_session_id is not None,
        "reset_file": f"data/{RESET_FILE}",
    }


def methods(db) -> dict:
    """Ce que la page de connexion peut proposer, sans rien révéler d'autre."""
    state = _state(db)
    return {"enabled": bool(state.enabled), "password": bool(state.password_hash),
            "passkey": db.query(AuthPasskey).count() > 0}


def _method_count(db, state) -> int:
    return db.query(AuthPasskey).count() + (1 if state.password_hash else 0)


def enable(db):
    global _enabled
    state = _state(db)
    if _method_count(db, state) == 0:
        raise AuthError("auth_no_method")
    state.enabled = True
    state.updated_at = datetime.utcnow()
    db.commit()
    with _lock:
        _enabled = True
    logger.info("Auth", "Connexion à l'interface activée")


def disable(db):
    """Les clés et le mot de passe restent enregistrés pour une prochaine activation ; les sessions sont fermées."""
    global _enabled
    state = _state(db)
    state.enabled = False
    state.updated_at = datetime.utcnow()
    db.query(AuthSession).delete(synchronize_session=False)
    db.commit()
    with _lock:
        _enabled = False
        _sessions.clear()
    logger.info("Auth", "Connexion à l'interface désactivée")


# ── Mot de passe ────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=_SCRYPT_MAXMEM, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64url(salt)}${b64url(digest)}"


def check_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = b64url_decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=b64url_decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(expected), maxmem=_SCRYPT_MAXMEM)
    except Exception:
        return False
    return hmac.compare_digest(got, expected)


def _valid_password(password) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN:
        raise AuthError("auth_password_short")
    if len(password) > PASSWORD_MAX:
        raise AuthError("auth_password_long")
    return password


def set_password(db, password):
    state = _state(db)
    state.password_hash = hash_password(_valid_password(password))
    state.updated_at = datetime.utcnow()
    db.commit()
    logger.info("Auth", "Mot de passe de l'interface enregistré")


def remove_password(db):
    state = _state(db)
    if state.enabled and db.query(AuthPasskey).count() == 0:
        raise AuthError("auth_last_method")
    state.password_hash = None
    state.updated_at = datetime.utcnow()
    db.commit()
    logger.info("Auth", "Mot de passe de l'interface supprimé")


def login_password(db, password) -> bool:
    state = _state(db)
    if not isinstance(password, str) or len(password) > PASSWORD_MAX:
        return False
    return check_password(password, state.password_hash)


# ── Sessions ────────────────────────────────────────────────────────────────

def open_session(db, method: str, user_agent: str = "", ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    now = datetime.utcnow()
    expires = now + timedelta(days=SESSION_DAYS)
    db.query(AuthSession).filter(AuthSession.expires_at < now).delete(synchronize_session=False)
    extra = db.query(AuthSession).order_by(AuthSession.last_seen_at.desc()).offset(SESSIONS_MAX - 1).all()
    for old in extra:
        db.delete(old)
    row = AuthSession(token_hash=token_hash, method=method, user_agent=(user_agent or "")[:300], ip=(ip or "")[:64],
                      created_at=now, last_seen_at=now, expires_at=expires)
    db.add(row)
    db.commit()
    with _lock:
        for old in extra:
            _sessions.pop(old.token_hash, None)
        for h in [h for h, e in _sessions.items() if e["expires_at"] < now]:
            _sessions.pop(h, None)
        _sessions[token_hash] = {"id": row.id, "expires_at": expires, "last_seen_at": now}
    return token


def check_session(token: Optional[str]):
    """(session, à_prolonger) pour un jeton valide, None sinon. Une session utilisée est prolongée de SESSION_DAYS ;
    la base n'est mise à jour qu'une fois par heure (à_prolonger), par l'appelant."""
    if not token or len(token) > 200:
        return None
    token_hash = _hash_token(token)
    now = datetime.utcnow()
    with _lock:
        entry = _sessions.get(token_hash)
        if not entry:
            return None
        if entry["expires_at"] < now:
            _sessions.pop(token_hash, None)
            return None
        touch = now - entry["last_seen_at"] >= TOUCH_EVERY
        if touch:
            entry["last_seen_at"] = now
            entry["expires_at"] = now + timedelta(days=SESSION_DAYS)
        return dict(entry, token_hash=token_hash), touch


def touch_session(token_hash: str, last_seen_at: datetime, expires_at: datetime):
    db = SessionLocal()
    try:
        db.query(AuthSession).filter(AuthSession.token_hash == token_hash).update(
            {AuthSession.last_seen_at: last_seen_at, AuthSession.expires_at: expires_at}, synchronize_session=False)
        db.commit()
    except Exception as e:
        logger.warning("Auth", f"Date de dernier usage d'une session : {e}")
    finally:
        db.close()


def close_session(db, token: Optional[str]):
    if not token:
        return
    token_hash = _hash_token(token)
    db.query(AuthSession).filter(AuthSession.token_hash == token_hash).delete(synchronize_session=False)
    db.commit()
    with _lock:
        _sessions.pop(token_hash, None)


def close_other_sessions(db, keep_id: Optional[int]) -> int:
    query = db.query(AuthSession)
    if keep_id is not None:
        query = query.filter(AuthSession.id != keep_id)
    count = query.delete(synchronize_session=False)
    db.commit()
    with _lock:
        for h in [h for h, e in _sessions.items() if e["id"] != keep_id]:
            _sessions.pop(h, None)
    return count


def cookie_header(token: str, secure: bool) -> str:
    return (f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else ""))


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def is_https(headers, scheme: str) -> bool:
    """Ce que voit le navigateur : derrière un reverse proxy HTTPS, Sentinel lui-même est joint en HTTP."""
    origin = headers.get("origin") or ""
    if origin.startswith("https://"):
        return True
    if (headers.get("x-forwarded-proto") or "").split(",")[0].strip() == "https":
        return True
    return scheme == "https"


def cross_site(headers) -> bool:
    """Requête qui modifie quelque chose envoyée par une autre page que Sentinel. Sec-Fetch-Site d'abord (tous les
    navigateurs récents), sinon Origin comparé à l'hôte demandé. Sans l'un ni l'autre, ce n'est pas un navigateur, donc
    pas le cookie de quelqu'un d'autre."""
    site = headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = headers.get("origin")
    if not origin or origin == "null":
        return bool(origin)
    host = urlsplit(origin).netloc.lower()
    allowed = {(headers.get("host") or "").lower()}
    forwarded = (headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    if forwarded:
        allowed.add(forwarded)
    return host not in allowed


# ── Limitation des tentatives ───────────────────────────────────────────────

class Limiter:
    """Par adresse : à partir de 5 échecs, une attente qui double de 1 à 15 minutes. Pour toutes les adresses à la fois :
    au plus 30 échecs par minute, contre des essais répartis. Remis à zéro par une connexion réussie depuis l'adresse."""
    PER_IP_FREE = 5
    BASE_SECONDS = 60
    MAX_SECONDS = 900
    GLOBAL_PER_MINUTE = 30

    def __init__(self):
        self._by_ip: dict = {}
        self._recent: deque = deque()
        self._lock = threading.Lock()

    def wait(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            while self._recent and now - self._recent[0] > 60:
                self._recent.popleft()
            waits = []
            entry = self._by_ip.get(ip)
            if entry and entry["until"] > now:
                waits.append(entry["until"] - now)
            if len(self._recent) >= self.GLOBAL_PER_MINUTE:
                waits.append(60 - (now - self._recent[0]))
            return int(max(waits) + 0.999) if waits else 0

    def failure(self, ip: str):
        now = time.monotonic()
        with self._lock:
            self._recent.append(now)
            entry = self._by_ip.setdefault(ip, {"count": 0, "until": 0.0, "at": now})
            entry["count"] += 1
            entry["at"] = now
            over = entry["count"] - self.PER_IP_FREE
            if over >= 0:
                entry["until"] = now + min(self.MAX_SECONDS, self.BASE_SECONDS * 2 ** min(over, 10))
            for old in [k for k, e in self._by_ip.items() if now - e["at"] > 86400]:
                self._by_ip.pop(old, None)

    def success(self, ip: str):
        with self._lock:
            self._by_ip.pop(ip, None)

    def reset(self):
        with self._lock:
            self._by_ip.clear()
            self._recent.clear()


limiter = Limiter()


# ── Clés d'accès (WebAuthn) ─────────────────────────────────────────────────

def passkey_context(origin: Optional[str]) -> tuple:
    """(rp_id, origin) pour une page ouverte à cette adresse. Le navigateur refuse lui-même une clé d'accès hors contexte
    sécurisé et sur une adresse IP : le dire ici donne un message clair au lieu d'une erreur du navigateur."""
    if not origin or origin == "null":
        raise AuthError("passkey_no_origin")
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if not host:
        raise AuthError("passkey_no_origin")
    try:
        ipaddress.ip_address(host)
        raise AuthError("passkey_ip_address")
    except ValueError:
        pass
    local = host == "localhost" or host.endswith(".localhost")
    if parts.scheme != "https" and not local:
        raise AuthError("passkey_needs_https")
    return host, f"{parts.scheme}://{parts.netloc}"


def _new_challenge(purpose: str, rp_id: str, origin: str, challenge: bytes) -> str:
    now = time.monotonic()
    challenge_id = secrets.token_urlsafe(16)
    with _lock:
        for k in [k for k, c in _challenges.items() if c["expires"] < now]:
            _challenges.pop(k, None)
        while len(_challenges) >= 100:
            _challenges.pop(next(iter(_challenges)))
        _challenges[challenge_id] = {"challenge": challenge, "rp_id": rp_id, "origin": origin, "purpose": purpose,
                                     "expires": now + CHALLENGE_SECONDS}
    return challenge_id


def _take_challenge(challenge_id, purpose: str, origin: str) -> dict:
    with _lock:
        entry = _challenges.pop(challenge_id, None) if isinstance(challenge_id, str) else None
    if not entry or entry["purpose"] != purpose or entry["expires"] < time.monotonic() or entry["origin"] != origin:
        raise AuthError("passkey_expired")
    return entry


def _user_handle(db, state) -> bytes:
    if not state.user_handle:
        state.user_handle = b64url(secrets.token_bytes(16))
        db.flush()
    return b64url_decode(state.user_handle)


def registration_options(db, origin: Optional[str], user_name: str) -> dict:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)
    rp_id, origin = passkey_context(origin)
    state = _state(db)
    existing = db.query(AuthPasskey).filter(AuthPasskey.rp_id == rp_id).all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Log to LLM Sentinel",
        user_id=_user_handle(db, state),
        user_name=user_name or "Sentinel",
        user_display_name=user_name or "Sentinel",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=b64url_decode(p.credential_id)) for p in existing],
    )
    db.commit()
    challenge_id = _new_challenge("register", rp_id, origin, options.challenge)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


def register_passkey(db, origin: Optional[str], challenge_id, name, credential) -> AuthPasskey:
    from webauthn import verify_registration_response
    rp_id, origin = passkey_context(origin)
    entry = _take_challenge(challenge_id, "register", origin)
    try:
        verified = verify_registration_response(credential=credential, expected_challenge=entry["challenge"],
                                                expected_rp_id=rp_id, expected_origin=origin)
    except Exception as e:
        logger.warning("Auth", f"Clé d'accès refusée à l'enregistrement : {e}")
        raise AuthError("passkey_invalid")
    credential_id = b64url(verified.credential_id)
    if db.query(AuthPasskey).filter(AuthPasskey.credential_id == credential_id).first():
        raise AuthError("passkey_exists")
    transports = []
    if isinstance(credential, dict):
        transports = [t for t in (credential.get("response") or {}).get("transports") or [] if isinstance(t, str)][:8]
    clean_name = (name or "").strip()[:60] if isinstance(name, str) else ""
    row = AuthPasskey(name=clean_name or "Passkey", credential_id=credential_id,
                      public_key=b64url(verified.credential_public_key), sign_count=verified.sign_count, rp_id=rp_id,
                      transports_json=json.dumps(transports), created_at=datetime.utcnow())
    db.add(row)
    db.commit()
    logger.info("Auth", f"Clé d'accès « {row.name} » enregistrée pour {rp_id}")
    return row


def authentication_options(db, origin: Optional[str]) -> dict:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
    rp_id, origin = passkey_context(origin)
    keys = db.query(AuthPasskey).filter(AuthPasskey.rp_id == rp_id).all()
    if not keys:
        raise AuthError("passkey_none_here")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=b64url_decode(p.credential_id),
                                                         transports=_transports(p)) for p in keys],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_id = _new_challenge("login", rp_id, origin, options.challenge)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


def _transports(passkey: AuthPasskey):
    from webauthn.helpers.structs import AuthenticatorTransport
    known = {t.value for t in AuthenticatorTransport}
    try:
        values = json.loads(passkey.transports_json or "[]")
    except ValueError:
        values = []
    return [AuthenticatorTransport(v) for v in values if v in known] or None


def login_passkey(db, origin: Optional[str], challenge_id, credential) -> bool:
    from webauthn import verify_authentication_response
    rp_id, origin = passkey_context(origin)
    entry = _take_challenge(challenge_id, "login", origin)
    credential_id = credential.get("id") if isinstance(credential, dict) else None
    key = None
    if isinstance(credential_id, str):
        key = db.query(AuthPasskey).filter(AuthPasskey.credential_id == credential_id,
                                           AuthPasskey.rp_id == rp_id).first()
    if not key:
        return False
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=entry["challenge"], expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=b64url_decode(key.public_key), credential_current_sign_count=key.sign_count or 0)
    except Exception as e:
        logger.warning("Auth", f"Clé d'accès « {key.name} » refusée : {e}")
        return False
    key.sign_count = verified.new_sign_count
    key.last_used_at = datetime.utcnow()
    db.commit()
    return True


def remove_passkey(db, passkey_id: int):
    key = db.query(AuthPasskey).filter(AuthPasskey.id == passkey_id).first()
    if not key:
        raise AuthError("passkey_not_found", status=404)
    state = _state(db)
    if state.enabled and _method_count(db, state) <= 1:
        raise AuthError("auth_last_method")
    db.delete(key)
    db.commit()
    logger.info("Auth", f"Clé d'accès « {key.name} » supprimée")
