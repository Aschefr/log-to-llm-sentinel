"""Garde de l'interface quand la connexion est activée (Configuration › Accès).

Tout demande une session, sauf ce qui doit rester joignable sans : la page de connexion et ses appels, les fichiers
statiques, la liste des langues, et la réception des logs par webhook (POST /api/webhook/logs/...), envoyés par des
machines qui n'ont pas de session. Middleware ASGI pur : il ne met pas en mémoire tampon les réponses en flux (chat, SSE).
"""
import asyncio
from urllib.parse import quote

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, RedirectResponse

from app.services import auth

OPEN_PATHS = {
    "/login",
    "/favicon.ico",
    "/api/i18n/languages",
    "/api/auth/methods",
    "/api/auth/login/options",
    "/api/auth/login/passkey",
    "/api/auth/login/password",
    "/api/auth/logout",
}
OPEN_PREFIXES = ("/static/",)
WEBHOOK_PREFIX = "/api/webhook/logs/"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _wants_json(path: str, method: str, headers: Headers) -> bool:
    if path.startswith("/api/") or path.startswith("/chat/api") or method not in ("GET", "HEAD"):
        return True
    return "text/html" not in (headers.get("accept") or "")


class AuthGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not auth.is_enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        method = scope.get("method", "GET")
        headers = Headers(scope=scope)

        if scope["type"] == "http" and method == "POST" and path.startswith(WEBHOOK_PREFIX):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and method not in SAFE_METHODS and auth.cross_site(headers):
            return await JSONResponse({"detail": "cross_site_refused"}, status_code=403)(scope, receive, send)
        found = auth.check_session(cookie_parser(headers.get("cookie") or "").get(auth.COOKIE_NAME))
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            if found:  # la page de connexion renvoie vers la suite une session déjà ouverte
                scope.setdefault("state", {})["auth_session"] = found[0]["id"]
            return await self.app(scope, receive, send)

        if not found:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            if _wants_json(path, method, headers):
                response = JSONResponse({"detail": "auth_required"}, status_code=401,
                                        headers={"X-Sentinel-Auth": "required"})
            else:
                target = path + ("?" + scope["query_string"].decode("latin-1") if scope.get("query_string") else "")
                response = RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)
            return await response(scope, receive, send)

        session, touched = found
        scope.setdefault("state", {})["auth_session"] = session["id"]
        if not touched or scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Session prolongée : la base en tâche de fond, le cookie avec la réponse
        asyncio.get_running_loop().run_in_executor(
            None, auth.touch_session, session["token_hash"], session["last_seen_at"], session["expires_at"])
        token = cookie_parser(headers.get("cookie") or "").get(auth.COOKIE_NAME)
        cookie = auth.cookie_header(token, auth.is_https(headers, scope.get("scheme", "http"))).encode("latin-1")

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [(b"set-cookie", cookie)]
            await send(message)

        return await self.app(scope, receive, send_with_cookie)
