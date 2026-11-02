"""Configuration et test du diagnostic en lecture seule (proxy Docker, SSH limité)."""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import GlobalConfig
from app.services import diagnostics as diag

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


class DiagSettings(BaseModel):
    docker_proxy_url: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None


class DockerTest(BaseModel):
    url: str


def _error(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def get_status():
    cfg = diag.settings()
    return {**cfg, "public_key": diag.public_key(), "available": diag.availability(cfg),
            "host_fingerprint": diag.host_fingerprint(), "host_checks": list(diag.HOST_CHECKS)}


@router.put("")
def update_settings(body: DiagSettings):
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            config = GlobalConfig()
            db.add(config)
        if body.docker_proxy_url is not None:
            url = body.docker_proxy_url.strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                raise HTTPException(status_code=422, detail="docker_proxy_url doit commencer par http:// ou https://")
            config.docker_proxy_url = url
        if body.ssh_host is not None:
            config.ssh_host = body.ssh_host.strip()
        if body.ssh_port is not None:
            if not 1 <= body.ssh_port <= 65535:
                raise HTTPException(status_code=422, detail="port SSH invalide")
            config.ssh_port = body.ssh_port
        if body.ssh_user is not None:
            config.ssh_user = body.ssh_user.strip()
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/docker/test")
async def test_docker(body: DockerTest):
    try:
        info = await asyncio.to_thread(diag.docker_test, body.url)
        return {"ok": True, **info}
    except diag.DiagnosticError as e:
        return {"ok": False, "error": str(e)}


@router.post("/ssh/key")
async def ssh_key(force: bool = False):
    try:
        return {"public_key": await asyncio.to_thread(diag.generate_key, force)}
    except (diag.DiagnosticError, OSError) as e:
        _error(e)


@router.get("/ssh/install")
def ssh_install(platform: str = "linux"):
    if platform not in ("linux", "unraid"):
        raise HTTPException(status_code=422, detail="platform: linux ou unraid")
    try:
        return {"platform": platform, "snippet": diag.install_snippet(platform)}
    except diag.DiagnosticError as e:
        _error(e)


@router.post("/ssh/test")
async def test_ssh():
    try:
        out = await asyncio.to_thread(diag.run_host_check, "overview")
        return {"ok": True, "output": out[:3000], "host_fingerprint": diag.host_fingerprint()}
    except diag.DiagnosticError as e:
        return {"ok": False, "error": str(e)}
