"""Connexions de diagnostic en lecture seule (proxies Docker, serveurs en SSH limité) et leurs tests."""
import asyncio
import ipaddress
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import DiagnosticLink, GlobalConfig, Rule
from app.services import cve_lookup, web_search
from app.services import diagnostics as diag

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


class LinkBody(BaseModel):
    name: str
    kind: str
    docker_url: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    web_url: Optional[str] = None
    # SSH : adresses mises dans from= du bloc copié, et ce qu'a renvoyé le test qui a précédé l'enregistrement
    ssh_source: Optional[str] = None
    ssh_script_version: Optional[str] = None
    ssh_client_ip: Optional[str] = None


class LinkBlock(BaseModel):
    ssh_source: str = ""


class WebTest(BaseModel):
    url: str


class CveSetting(BaseModel):
    enabled: bool


class DockerTest(BaseModel):
    url: str


class SecondLookSetting(BaseModel):
    enabled: Optional[bool] = None
    include_info: Optional[bool] = None
    may_lower: Optional[bool] = None


class KeyImport(BaseModel):
    private_key: str


class SshTest(BaseModel):
    ssh_host: str
    ssh_port: int = 22
    ssh_user: str
    link_id: Optional[int] = None  # connexion enregistrée testée : sa version de script et l'adresse vue sont retenues


def _error(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


def _link_values(db, body: LinkBody, link_id: Optional[int] = None) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="link_name_required")
    same = db.query(DiagnosticLink).filter(DiagnosticLink.id != (link_id or 0)).all()
    if any(l.name.lower() == name.lower() for l in same):
        raise HTTPException(status_code=422, detail="link_name_taken")
    if body.kind == "docker":
        url = (body.docker_url or "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="link_url_invalid")
        return {"name": name, "kind": "docker", "docker_url": url}
    if body.kind == "ssh":
        host, user, port = (body.ssh_host or "").strip(), (body.ssh_user or "").strip(), body.ssh_port or 22
        if not host or not user:
            raise HTTPException(status_code=422, detail="link_host_required")
        if not 1 <= port <= 65535:
            raise HTTPException(status_code=422, detail="link_port_invalid")
        values = {"name": name, "kind": "ssh", "ssh_host": host, "ssh_port": port, "ssh_user": user}
        if body.ssh_source is not None:
            try:
                values["ssh_source"] = diag.check_source(body.ssh_source) or ""
            except diag.DiagnosticError as e:
                raise HTTPException(status_code=422, detail=str(e))
        values.update(_tested_values(body.ssh_script_version, body.ssh_client_ip))
        return values
    if body.kind == "web":
        url = (body.web_url or "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="link_url_invalid")
        return {"name": name, "kind": "web", "web_url": url}
    raise HTTPException(status_code=422, detail="link_kind_invalid")


def _tested_values(version: Optional[str], client_ip: Optional[str]) -> dict:
    """What a successful test reported, as stored on the connection. A script without a number is stored as "0"."""
    out = {}
    if version is not None:
        out["ssh_script_version"] = (str(version).strip() or "0")[:12]
    if client_ip is not None:
        try:
            out["ssh_client_ip"] = str(ipaddress.ip_address(client_ip.strip())) if client_ip.strip() else ""
        except ValueError:
            out["ssh_client_ip"] = ""
    return out


@router.get("")
def get_status():
    db = SessionLocal()
    try:
        rows = db.query(DiagnosticLink).order_by(DiagnosticLink.kind, DiagnosticLink.id).all()
        links = [{**diag.link_dict(l), **(diag.ssh_link_details(l) if l.kind == "ssh" else {})} for l in rows]
        rules = [(r.id, r.name, r.get_diagnostic_link_ids()) for r in db.query(Rule).order_by(Rule.name).all()]
        for link in links:
            link["rules"] = [name for _, name, ids in rules if ids is None or link["id"] in ids]
            link["rule_ids"] = [rid for rid, _, ids in rules if ids is None or link["id"] in ids]
            if link["kind"] == "ssh":
                link["host_fingerprint"] = diag.host_fingerprint(link["ssh_host"], link["ssh_port"])
        config = db.query(GlobalConfig).first()
        second_look = {"enabled": bool(config and config.second_look_enabled),
                       "include_info": bool(config and config.second_look_include_info),
                       "may_lower": bool(config and config.second_look_may_lower)}
        return {"cve_lookup": cve_lookup.enabled(db), "web_search": web_search.status(), "second_look": second_look,
                "links": links, "rule_count": len(rules),
                "all_rules": [{"id": rid, "name": name} for rid, name, _ in rules],
                "public_key": diag.public_key(), "available": diag.availability(), "host_checks": list(diag.HOST_CHECKS)}
    finally:
        db.close()


@router.post("/links")
def add_link(body: LinkBody):
    db = SessionLocal()
    try:
        link = DiagnosticLink(**_link_values(db, body))
        db.add(link)
        db.commit()
        return diag.link_dict(link)
    finally:
        db.close()


@router.put("/links/{link_id}")
def update_link(link_id: int, body: LinkBody):
    db = SessionLocal()
    try:
        link = db.query(DiagnosticLink).filter(DiagnosticLink.id == link_id).first()
        if not link:
            raise HTTPException(status_code=404, detail="link_not_found")
        for key, value in _link_values(db, body, link_id).items():
            setattr(link, key, value)
        db.commit()
        return diag.link_dict(link)
    finally:
        db.close()


class LinkRules(BaseModel):
    rule_ids: List[int]


@router.put("/links/{link_id}/block")
def set_link_block(link_id: int, body: LinkBlock):
    """Remembers the addresses put in from= of the block just copied for this server, to pre-fill it next time."""
    db = SessionLocal()
    try:
        link = db.query(DiagnosticLink).filter(DiagnosticLink.id == link_id, DiagnosticLink.kind == "ssh").first()
        if not link:
            raise HTTPException(status_code=404, detail="link_not_found")
        try:
            link.ssh_source = diag.check_source(body.ssh_source) or ""
        except diag.DiagnosticError as e:
            raise HTTPException(status_code=422, detail=str(e))
        db.commit()
        return {"ssh_source": link.ssh_source}
    finally:
        db.close()


@router.put("/links/{link_id}/rules")
def set_link_rules(link_id: int, body: LinkRules):
    """Gives this connection to exactly these rules, from the connection side. Each rule keeps its other connections.
    A rule that ends up with every connection goes back to "all", as when saved from the rule form."""
    db = SessionLocal()
    try:
        if not db.query(DiagnosticLink).filter(DiagnosticLink.id == link_id).first():
            raise HTTPException(status_code=404, detail="link_not_found")
        all_ids = [l.id for l in db.query(DiagnosticLink).order_by(DiagnosticLink.id).all()]
        wanted = set(body.rule_ids)
        for rule in db.query(Rule).all():
            ids = rule.get_diagnostic_link_ids()
            if (rule.id in wanted) == (ids is None or link_id in ids):
                continue
            if rule.id in wanted:
                new = sorted(set(ids) | {link_id})
                rule.diagnostic_link_ids_json = None if set(all_ids) <= set(new) else json.dumps(new)
            else:
                rule.diagnostic_link_ids_json = json.dumps([i for i in (all_ids if ids is None else ids) if i != link_id])
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/links/{link_id}")
def delete_link(link_id: int):
    db = SessionLocal()
    try:
        if not db.query(DiagnosticLink).filter(DiagnosticLink.id == link_id).delete():
            raise HTTPException(status_code=404, detail="link_not_found")
        for rule in db.query(Rule).filter(Rule.diagnostic_link_ids_json != None).all():
            rule.diagnostic_link_ids_json = json.dumps([i for i in rule.get_diagnostic_link_ids() if i != link_id])
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/web/test")
async def test_web(body: WebTest):
    try:
        return {"ok": True, **await asyncio.to_thread(web_search.test_instance, body.url.strip())}
    except web_search.WebSearchError as e:
        return {"ok": False, "error": str(e)}


@router.post("/cve")
def set_cve_lookup(body: CveSetting):
    """Fiches CVE : seul le numéro du CVE part vers NVD, mais c'est une sortie vers Internet, donc au choix de l'utilisateur."""
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            raise HTTPException(status_code=404, detail="config_not_found")
        config.cve_lookup_enabled = body.enabled
        db.commit()
        return {"enabled": body.enabled}
    finally:
        db.close()


@router.post("/second-look")
def set_second_look(body: SecondLookSetting):
    """Second regard pendant l'analyse automatique : contrôles en lecture seule sur les connexions de la règle."""
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            raise HTTPException(status_code=404, detail="config_not_found")
        for key in ("enabled", "include_info", "may_lower"):
            value = getattr(body, key)
            if value is not None:
                setattr(config, f"second_look_{key}", value)
        db.commit()
        return {"enabled": bool(config.second_look_enabled), "include_info": bool(config.second_look_include_info),
                "may_lower": bool(config.second_look_may_lower)}
    finally:
        db.close()


@router.post("/cve/test")
def test_cve():
    db = SessionLocal()
    try:
        data = cve_lookup.lookup(db, "CVE-2021-44228")
        if data.get("error"):
            return {"ok": False, "error": "NVD injoignable"}
        return {"ok": True, "found": bool(data.get("found"))}
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


@router.post("/ssh/key/import")
async def ssh_key_import(body: KeyImport):
    try:
        return {"public_key": await asyncio.to_thread(diag.import_key, body.private_key)}
    except (diag.DiagnosticError, OSError) as e:
        _error(e)


@router.get("/ssh/install")
def ssh_install(platform: str = "linux", user: str = diag.DEFAULT_ACCOUNT, source: str = ""):
    """source : adresses de Sentinel vues par le serveur ; la clé n'y sera acceptée que depuis elles (option from=)."""
    if platform not in ("linux", "unraid"):
        raise HTTPException(status_code=422, detail="platform: linux ou unraid")
    try:
        return {"platform": platform, "snippet": diag.install_snippet(platform, user, source)}
    except diag.DiagnosticError as e:
        _error(e)


@router.get("/ssh/uninstall")
def ssh_uninstall(platform: str = "linux", user: str = diag.DEFAULT_ACCOUNT):
    if platform not in ("linux", "unraid"):
        raise HTTPException(status_code=422, detail="platform: linux ou unraid")
    try:
        return {"platform": platform, "snippet": diag.uninstall_snippet(platform, user)}
    except diag.DiagnosticError as e:
        _error(e)


@router.post("/ssh/test")
async def test_ssh(body: SshTest):
    """Teste les valeurs saisies sans les enregistrer."""
    cfg = {"ssh_host": body.ssh_host.strip(), "ssh_port": body.ssh_port, "ssh_user": body.ssh_user.strip()}
    if not 1 <= int(cfg["ssh_port"]) <= 65535:
        return {"ok": False, "error": "port SSH invalide"}
    try:
        out = await asyncio.to_thread(diag.run_host_check, cfg, "overview")
        info = await asyncio.to_thread(diag.installed_script_info, cfg)
        version = info["version"]
        if body.link_id:
            db = SessionLocal()
            try:
                link = db.query(DiagnosticLink).filter(DiagnosticLink.id == body.link_id, DiagnosticLink.kind == "ssh").first()
                if link:
                    for key, value in _tested_values(version or "", info["client_ip"] or "").items():
                        setattr(link, key, value)
                    db.commit()
            finally:
                db.close()
        return {"ok": True, "output": out[:3000], "host_fingerprint": diag.host_fingerprint(cfg["ssh_host"], cfg["ssh_port"]),
                "script_version": version, "script_outdated": version != diag.DIAG_SCRIPT_VERSION,
                "client_ip": info["client_ip"]}
    except diag.DiagnosticError as e:
        return {"ok": False, "error": str(e)}
