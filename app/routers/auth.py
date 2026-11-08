"""Connexion à l'interface : réglages (Configuration › Accès), connexion et déconnexion. Voir services/auth.py."""
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app import logger
from app.database import SessionLocal
from app.models import GlobalConfig
from app.services import auth
from app.services.auth import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _error(e: AuthError) -> JSONResponse:
    return JSONResponse({"detail": e.code, **e.extra}, status_code=e.status)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _current_session(request: Request):
    return getattr(request.state, "auth_session", None)


def _with_session(db, request: Request, method: str, body: dict) -> JSONResponse:
    token = auth.open_session(db, method, request.headers.get("user-agent", ""), _client_ip(request))
    response = JSONResponse(body)
    response.headers.append("set-cookie", auth.cookie_header(token, auth.is_https(request.headers, request.url.scheme)))
    return response


def _run(fn):
    db = SessionLocal()
    try:
        return fn(db)
    except AuthError as e:
        return _error(e)
    finally:
        db.close()


@router.get("/methods")
def get_methods():
    return _run(auth.methods)


@router.get("/status")
def get_status(request: Request):
    return _run(lambda db: auth.status(db, _current_session(request)))


@router.post("/password")
def set_password(payload: dict = Body(...)):
    def go(db):
        auth.set_password(db, payload.get("password"))
        return {"ok": True}
    return _run(go)


@router.delete("/password")
def remove_password():
    def go(db):
        auth.remove_password(db)
        return {"ok": True}
    return _run(go)


@router.post("/passkeys/options")
def passkey_options(request: Request):
    def go(db):
        config = db.query(GlobalConfig).first()
        name = (config.instance_name if config and config.instance_name else "") or "Sentinel"
        return auth.registration_options(db, request.headers.get("origin"), name)
    return _run(go)


@router.post("/passkeys")
def add_passkey(request: Request, payload: dict = Body(...)):
    def go(db):
        row = auth.register_passkey(db, request.headers.get("origin"), payload.get("challenge_id"),
                                    payload.get("name"), payload.get("credential"))
        return {"ok": True, "id": row.id}
    return _run(go)


@router.delete("/passkeys/{passkey_id}")
def remove_passkey(passkey_id: int):
    def go(db):
        auth.remove_passkey(db, passkey_id)
        return {"ok": True}
    return _run(go)


@router.post("/enable")
def enable(request: Request):
    """La session de ce navigateur s'ouvre au même moment : activer ne peut pas fermer la porte à celui qui active."""
    def go(db):
        auth.enable(db)
        return _with_session(db, request, "setup", {"ok": True})
    return _run(go)


@router.post("/disable")
def disable():
    def go(db):
        auth.disable(db)
        response = JSONResponse({"ok": True})
        response.headers.append("set-cookie", auth.clear_cookie_header())
        return response
    return _run(go)


@router.post("/sessions/close-others")
def close_others(request: Request):
    return _run(lambda db: {"ok": True, "closed": auth.close_other_sessions(db, _current_session(request))})


def _too_many(request: Request):
    wait = auth.limiter.wait(_client_ip(request))
    if wait:
        return JSONResponse({"detail": "too_many_attempts", "retry_after": wait}, status_code=429,
                            headers={"Retry-After": str(wait)})
    return None


def _failed(request: Request, method: str) -> JSONResponse:
    ip = _client_ip(request)
    auth.limiter.failure(ip)
    logger.warning("Auth", f"Échec de connexion ({method}) depuis {ip or 'adresse inconnue'}")
    return JSONResponse({"detail": "login_failed"}, status_code=401)


@router.post("/login/password")
def login_password(request: Request, payload: dict = Body(...)):
    if not auth.is_enabled():
        return JSONResponse({"detail": "auth_disabled"}, status_code=400)
    refused = _too_many(request)
    if refused:
        return refused

    def go(db):
        if not auth.login_password(db, payload.get("password")):
            return _failed(request, "mot de passe")
        auth.limiter.success(_client_ip(request))
        return _with_session(db, request, "password", {"ok": True})
    return _run(go)


@router.post("/login/options")
def login_options(request: Request):
    if not auth.is_enabled():
        return JSONResponse({"detail": "auth_disabled"}, status_code=400)
    refused = _too_many(request)
    if refused:
        return refused
    return _run(lambda db: auth.authentication_options(db, request.headers.get("origin")))


@router.post("/login/passkey")
def login_passkey(request: Request, payload: dict = Body(...)):
    if not auth.is_enabled():
        return JSONResponse({"detail": "auth_disabled"}, status_code=400)
    refused = _too_many(request)
    if refused:
        return refused

    def go(db):
        if not auth.login_passkey(db, request.headers.get("origin"), payload.get("challenge_id"), payload.get("credential")):
            return _failed(request, "clé d'accès")
        auth.limiter.success(_client_ip(request))
        return _with_session(db, request, "passkey", {"ok": True})
    return _run(go)


@router.post("/logout")
def logout(request: Request):
    def go(db):
        auth.close_session(db, request.cookies.get(auth.COOKIE_NAME))
        response = JSONResponse({"ok": True})
        response.headers.append("set-cookie", auth.clear_cookie_header())
        return response
    return _run(go)
