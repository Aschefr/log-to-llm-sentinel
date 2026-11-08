"""
Diagnostic en lecture seule de l'infrastructure, pour le chat et l'investigation :

- Docker : via un proxy du socket Docker qui n'autorise que les requêtes de lecture
  (ex. tecnativa/docker-socket-proxy avec CONTAINERS=1, POST=0). Sentinel ne monte jamais le socket.
- Serveur : via SSH, avec une clé générée par Sentinel et limitée côté serveur à un script
  (option `command=` d'authorized_keys). Sentinel ne transmet qu'un nom de contrôle d'une liste fermée.

Les sorties sont bornées et les variables d'environnement des conteneurs ne sont jamais renvoyées.
"""
import ipaddress
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx

from app.database import SessionLocal
from app.models import Analysis, ChatConversation, DiagnosticLink, GlobalConfig, Rule

DOCKER_TIMEOUT_S = 8.0
SSH_TIMEOUT_S = 20
MAX_LOG_LINES = 200
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,80}$")

# Contrôles serveur : nom → prend un argument (nom de service). Doit correspondre au script installé sur le serveur.
HOST_CHECKS = {
    "overview": False, "disk": False, "memory": False, "load": False, "processes": False,
    "mounts": False, "network": False, "kernel": False, "syslog": False, "array": False,
    "failed-services": False, "service": True, "journal": True, "fstab": False, "nfs-exports": True,
}
# Argument d'un contrôle : un nom de service, ou un serveur NFS (nom ou adresse)
HOST_ARG_RE = {"nfs-exports": re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")}


class DiagnosticError(Exception):
    """Erreur présentable à l'utilisateur ou au LLM."""


# ── Réglages ────────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data"))


def ssh_dir() -> Path:
    return _data_dir() / "ssh"


def key_path() -> Path:
    return ssh_dir() / "sentinel_diag_ed25519"


def link_dict(link: DiagnosticLink) -> dict:
    return {"id": link.id, "name": link.name, "kind": link.kind, "docker_url": link.docker_url or "",
            "ssh_host": link.ssh_host or "", "ssh_port": link.ssh_port or 22, "ssh_user": link.ssh_user or "",
            "web_url": link.web_url or ""}


def ssh_link_details(link: DiagnosticLink) -> dict:
    """What the interface needs to rebuild this server's own blocks, and whether its script needs re-pasting.
    The platform follows from the user: the Linux block never creates root, Unraid always uses it."""
    version = link.ssh_script_version or ""
    return {"ssh_platform": "unraid" if (link.ssh_user or "") == "root" else "linux",
            "ssh_source": link.ssh_source or "", "ssh_client_ip": link.ssh_client_ip or "",
            "script_version": version, "script_outdated": bool(version) and version != DIAG_SCRIPT_VERSION}


def all_links(db, kind: Optional[str] = None) -> List[dict]:
    """Connexions utilisables. Sans la clé de Sentinel, aucune connexion SSH ne peut servir."""
    rows = db.query(DiagnosticLink).order_by(DiagnosticLink.kind, DiagnosticLink.id).all()
    return [link_dict(r) for r in rows if (not kind or r.kind == kind) and (r.kind != "ssh" or key_path().exists())]


def links_for_rule(db, rule: Rule, kind: Optional[str] = None) -> List[dict]:
    allowed = rule.get_diagnostic_link_ids()
    return [link for link in all_links(db, kind) if allowed is None or link["id"] in allowed]


def links_for_conversation(db, conversation_id: Optional[int], kind: Optional[str] = None) -> List[dict]:
    """Une conversation née d'une alerte n'a accès qu'aux connexions de sa règle ; une conversation libre, à toutes."""
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first() if conversation_id else None
    analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first() if conv and conv.analysis_id else None
    rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first() if analysis else None
    return links_for_rule(db, rule, kind) if rule else all_links(db, kind)


def availability() -> dict:
    db = SessionLocal()
    try:
        return {"docker": bool(all_links(db, "docker")), "host": bool(all_links(db, "ssh")), "web": bool(all_links(db, "web"))}
    finally:
        db.close()


def migrate_legacy(db):
    """L'ancienne configuration unique devient des connexions, et les règles sans choix continuent d'y avoir accès."""
    config = db.query(GlobalConfig).first()
    if not config:
        return
    url = (config.docker_proxy_url or "").strip().rstrip("/")
    if url:
        db.add(DiagnosticLink(name="Docker", kind="docker", docker_url=url))
    if (config.ssh_host or "").strip() and (config.ssh_user or "").strip():
        db.add(DiagnosticLink(name="Serveur", kind="ssh", ssh_host=config.ssh_host.strip(), ssh_port=config.ssh_port or 22, ssh_user=config.ssh_user.strip()))
    config.docker_proxy_url, config.ssh_host, config.ssh_user = "", "", ""
    db.commit()


# ── Docker (proxy en lecture seule) ─────────────────────────────────────────────

def _docker_get(base: str, path: str, params: dict = None, raw: bool = False):
    try:
        with httpx.Client(base_url=base, timeout=DOCKER_TIMEOUT_S) as client:
            resp = client.get(path, params=params)
    except httpx.HTTPError as e:
        raise DiagnosticError(f"proxy Docker injoignable ({type(e).__name__})")
    if resp.status_code == 404:
        raise DiagnosticError("conteneur introuvable")
    if resp.status_code == 403:
        raise DiagnosticError("requête refusée par le proxy Docker (vérifier CONTAINERS=1)")
    if resp.status_code >= 400:
        raise DiagnosticError(f"erreur du proxy Docker (HTTP {resp.status_code})")
    return resp.content if raw else resp.json()


def _check_container(name: str) -> str:
    name = (name or "").strip().lstrip("/")
    if not CONTAINER_RE.match(name):
        raise DiagnosticError("nom de conteneur invalide")
    return name


def docker_test(base: str) -> dict:
    """Seul CONTAINERS est exigé : c'est ce que les outils utilisent, et /info est refusé par défaut sur ces proxys."""
    base = base.strip().rstrip("/")
    rows = _docker_get(base, "/containers/json", {"all": "true"})
    try:
        version = _docker_get(base, "/version").get("Version")
    except DiagnosticError:
        version = None
    return {"server_version": version, "containers": len(rows), "running": sum(c.get("State") == "running" for c in rows)}


def docker_containers(base: str, all_containers: bool = True) -> list:
    rows = _docker_get(base, "/containers/json", {"all": "true" if all_containers else "false"})
    return [{
        "name": (c.get("Names") or ["?"])[0].lstrip("/"),
        "image": c.get("Image"),
        "state": c.get("State"),
        "status": c.get("Status"),  # ex. "Up 3 hours (healthy)", "Restarting (1) 5 seconds ago"
    } for c in rows]


def docker_container_status(base: str, name: str) -> dict:
    d = _docker_get(base, f"/containers/{_check_container(name)}/json")
    state = d.get("State") or {}
    health = state.get("Health") or {}
    return {
        "name": (d.get("Name") or "").lstrip("/"),
        "image": (d.get("Config") or {}).get("Image"),
        "status": state.get("Status"),
        "running": state.get("Running"),
        "restarting": state.get("Restarting"),
        "oom_killed": state.get("OOMKilled"),
        "exit_code": state.get("ExitCode"),
        "error": state.get("Error") or None,
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "restart_count": d.get("RestartCount"),
        "restart_policy": ((d.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name"),
        "health": {
            "status": health.get("Status"),
            "failing_streak": health.get("FailingStreak"),
            "last_checks": [{"at": h.get("End"), "exit": h.get("ExitCode"), "output": (h.get("Output") or "").strip()[-300:]}
                            for h in (health.get("Log") or [])[-3:]],
        } if health else None,
        "mounts": [f"{m.get('Source')} → {m.get('Destination')}" for m in (d.get("Mounts") or [])][:15],
        "networks": list(((d.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
        # Variables d'environnement et labels volontairement omis (secrets fréquents)
    }


def _demux_logs(data: bytes) -> str:
    """Flux de logs Docker : multiplexé (en-têtes de 8 octets) quand le conteneur n'a pas de TTY."""
    if len(data) >= 8 and data[0] in (0, 1, 2) and data[1:4] == b"\x00\x00\x00":
        out, i = [], 0
        while i + 8 <= len(data):
            size = int.from_bytes(data[i + 4:i + 8], "big")
            out.append(data[i + 8:i + 8 + size])
            i += 8 + size
        data = b"".join(out)
    return data.decode("utf-8", errors="replace")


def docker_container_logs(base: str, name: str, lines: int = 100, since_minutes: Optional[int] = None) -> str:
    lines = max(1, min(int(lines or 100), MAX_LOG_LINES))
    params = {"stdout": "true", "stderr": "true", "timestamps": "true", "tail": str(lines)}
    if since_minutes:
        params["since"] = str(int(datetime.utcnow().timestamp()) - int(since_minutes) * 60)
    return _demux_logs(_docker_get(base, f"/containers/{_check_container(name)}/logs", params, raw=True)).strip()


def docker_container_stats(base: str, name: str) -> dict:
    s = _docker_get(base, f"/containers/{_check_container(name)}/stats", {"stream": "false"})
    cpu, pre = s.get("cpu_stats") or {}, s.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    sys_delta = (cpu.get("system_cpu_usage") or 0) - (pre.get("system_cpu_usage") or 0)
    cpus = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    mem = s.get("memory_stats") or {}
    cache = (mem.get("stats") or {}).get("inactive_file", (mem.get("stats") or {}).get("cache", 0))
    used = max(0, (mem.get("usage") or 0) - (cache or 0))
    net = s.get("networks") or {}
    return {
        "cpu_percent": round(cpu_delta / sys_delta * cpus * 100, 1) if sys_delta > 0 and cpu_delta >= 0 else None,
        "memory_mb": round(used / 1048576, 1),
        "memory_limit_mb": round((mem.get("limit") or 0) / 1048576, 1),
        "pids": (s.get("pids_stats") or {}).get("current"),
        "net_rx_mb": round(sum(n.get("rx_bytes", 0) for n in net.values()) / 1048576, 1),
        "net_tx_mb": round(sum(n.get("tx_bytes", 0) for n in net.values()) / 1048576, 1),
    }


# ── Serveur (SSH limité à un script) ────────────────────────────────────────────

def public_key() -> Optional[str]:
    pub = key_path().with_suffix(".pub")
    return pub.read_text(encoding="utf-8").strip() if pub.exists() else None


def generate_key(force: bool = False) -> str:
    if not shutil.which("ssh-keygen"):
        raise DiagnosticError("ssh-keygen introuvable dans le conteneur")
    ssh_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir(), 0o700)
    if key_path().exists() and not force:
        return public_key()
    for p in (key_path(), key_path().with_suffix(".pub"), ssh_dir() / "known_hosts"):
        if p.exists():
            p.unlink()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "sentinel-diag", "-f", str(key_path())],
                   check=True, capture_output=True, timeout=30)
    os.chmod(key_path(), 0o600)
    return public_key()


def import_key(private_key: str) -> str:
    """Uses a private key pasted by the user (one from an earlier install, say) instead of a generated one."""
    if not shutil.which("ssh-keygen"):
        raise DiagnosticError("ssh-keygen introuvable dans le conteneur")
    text = (private_key or "").replace("\r\n", "\n").strip() + "\n"
    if text.startswith(("ssh-", "ecdsa-", "sk-")) and "PRIVATE KEY" not in text:
        raise DiagnosticError(
            "c'est la clé publique, celle que le serveur autorise. Il faut la clé privée qui va avec : "
            "le fichier data/ssh/sentinel_diag_ed25519 de l'autre Sentinel, par exemple "
            "/mnt/user/appdata/log-to-llm-sentinel/data/ssh/sentinel_diag_ed25519 sur Unraid")
    if "PRIVATE KEY-----" not in text or len(text) > 20000:
        raise DiagnosticError("colle la clé privée entière, de -----BEGIN à -----END")
    ssh_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir(), 0o700)
    tmp = ssh_dir() / "import_tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        res = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(tmp)], capture_output=True, text=True, timeout=30)
        pub = res.stdout.strip()
        if res.returncode != 0 or not pub.startswith(("ssh-", "ecdsa-")):
            raise DiagnosticError("clé illisible, ou protégée par une phrase de passe : Sentinel a besoin d'une clé sans phrase de passe")
        if len(pub.split()) < 3:
            pub += " sentinel-diag"
        os.replace(tmp, key_path())
        key_path().with_suffix(".pub").write_text(pub + "\n", encoding="utf-8")
    finally:
        if tmp.exists():
            tmp.unlink()
    os.chmod(key_path(), 0o600)
    return public_key()


def run_host_check(cfg: dict, check: str, arg: Optional[str] = None, _raw: Optional[str] = None) -> str:
    if not (cfg.get("ssh_host") and cfg.get("ssh_user") and key_path().exists()):
        raise DiagnosticError("diagnostic serveur non configuré")
    if check not in HOST_CHECKS:
        raise DiagnosticError(f"contrôle inconnu « {check} » (disponibles : {', '.join(HOST_CHECKS)})")
    words = [_raw] if _raw else [check]
    if HOST_CHECKS[check] and not _raw:
        if not arg or not HOST_ARG_RE.get(check, UNIT_RE).match(arg):
            raise DiagnosticError("nom de serveur invalide" if check == "nfs-exports" else "nom de service invalide")
        words.append(arg)
    cmd = ["ssh", "-i", str(key_path()), "-p", str(int(cfg["ssh_port"])),
           "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={ssh_dir() / 'known_hosts'}",
           f"{cfg['ssh_user']}@{cfg['ssh_host']}", " ".join(words)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=SSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise DiagnosticError(f"le serveur n'a pas répondu en {SSH_TIMEOUT_S} s")
    out = res.stdout.decode("utf-8", errors="replace").strip()
    err = res.stderr.decode("utf-8", errors="replace").strip()
    if res.returncode == 255:
        raise DiagnosticError(f"connexion SSH impossible : {err.splitlines()[-1] if err else 'erreur inconnue'}")
    return out or err or "(aucune sortie)"


def host_fingerprint(host: str, port: int = 22) -> Optional[str]:
    kh = ssh_dir() / "known_hosts"
    if not kh.exists() or not shutil.which("ssh-keygen"):
        return None
    target = host if int(port) == 22 else f"[{host}]:{int(port)}"
    res = subprocess.run(["ssh-keygen", "-l", "-F", target, "-f", str(kh)], capture_output=True, timeout=10)
    lines = [l for l in res.stdout.decode("utf-8", errors="replace").splitlines() if l and not l.startswith("#")]
    return lines[0].strip() if res.returncode == 0 and lines else None


# ── Script installé sur le serveur ──────────────────────────────────────────────

DIAG_SCRIPT_VERSION = "4"  # à incrémenter à chaque modification du script : Sentinel signale un script à mettre à jour

DIAG_SCRIPT = r'''#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Log-to-LLM Sentinel — script de diagnostic en LECTURE SEULE
# La clé SSH de Sentinel ne peut lancer que ce script (option command= de la clé).
# Sentinel choisit un contrôle dans la liste ci-dessous, et rien d'autre :
# aucune commande ici n'écrit, ne redémarre ni ne modifie quoi que ce soit.
# ══════════════════════════════════════════════════════════════════════════════
set -u
SCRIPT_VERSION=@VERSION@

# Contrôle demandé par Sentinel, par exemple « disk » ou « service nginx »
read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-help}"
CHECK="${ARGS[0]:-help}"
ARG="${ARGS[1]:-}"
CLIENT_IP="${SSH_CONNECTION:-}"; CLIENT_IP="${CLIENT_IP%% *}"

# ── Outils internes ───────────────────────────────────────────────────────────
# Nom de service accepté : lettres, chiffres et @ . _ : - uniquement
valid_unit() { [[ "$1" =~ ^[A-Za-z0-9@._:-]{1,80}$ ]]; }
# Serveur NFS accepté : seulement un serveur dont cette machine monte déjà un partage (fstab ou montages actifs)
known_nfs_server() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$ ]] || return 1
  awk -v h="$1:" 'index($1, h) == 1 { f = 1 } END { exit !f }' /etc/fstab /proc/mounts 2>/dev/null
}
has_systemd() { command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }
no_systemd() {
  if [ -f /etc/unraid-version ]; then echo "Unraid n'utilise pas systemd : utiliser les contrôles syslog, array, processes ou les outils Docker."
  else echo "systemd indisponible sur ce système"; fi
}
# Retire du journal les connexions SSH de Sentinel lui-même
drop_own_ssh() {
  [ -n "$CLIENT_IP" ] || { cat "$1"; return; }
  local pids
  pids=$(grep -F "$CLIENT_IP" "$1" 2>/dev/null | grep -oE 'sshd(-session)?\[[0-9]+\]' | grep -oE '[0-9]+' | sort -u | paste -sd'|')
  if [ -n "$pids" ]; then grep -vE "sshd(-session)?\[($pids)\]" "$1"; else cat "$1"; fi
}
# Bruit sans intérêt pour un diagnostic : interfaces réseau de Docker, ouvertures de session
DOCKER_NET='br-[0-9a-f]{12}|docker0'
KERNEL_NOISE="veth[0-9a-f]+|$DOCKER_NET|entered (blocking|forwarding|disabled) state|(allmulticast|promiscuous) mode|renamed from veth|elogind.*(New|Removed) session"
# Systèmes de fichiers virtuels ignorés par df
DF_EXCL="-x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs"

# ── Contrôles disponibles ─────────────────────────────────────────────────────
case "$CHECK" in
  # Vue d'ensemble : charge, mémoire, espace disque
  overview)  uptime; echo; free -m; echo; df -hT $DF_EXCL 2>/dev/null ;;
  # Espace disque et inodes
  disk)      df -hT $DF_EXCL 2>/dev/null; echo; df -i $DF_EXCL 2>/dev/null | head -n 20 ;;
  # Mémoire et swap
  memory)    free -m; echo; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|Dirty|Committed_AS)' /proc/meminfo ;;
  # Charge processeur
  load)      uptime; echo "CPU : $(nproc)"; cat /proc/loadavg ;;
  # Processus les plus gourmands (CPU puis mémoire), sans ce script lui-même
  processes) ps -eo pid,ppid,user,%cpu,%mem,etime,comm --sort=-%cpu | awk -v me=$$ 'NR==1 || ($1!=me && $2!=me && $7!~/^sshd/)' | cut -c1-120 | head -n 16
             echo; ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -n 11 ;;
  # Partages réseau montés (NFS, SMB, shfs…)
  mounts)    (findmnt -rn -t nfs,nfs4,cifs,smb3,fuse.sshfs,fuse.shfs -o TARGET,SOURCE,FSTYPE 2>/dev/null) || mount | grep -E ' (nfs|nfs4|cifs|smb3|fuse\.[a-z]+) ' ;;
  # Adresses et routes réseau, sans les interfaces de Docker
  network)   ip -br addr 2>/dev/null | grep -vE "^(veth|$DOCKER_NET|vhost)"; echo; ip route 2>/dev/null | grep -vE "$DOCKER_NET" | head -n 20 ;;
  # 50 derniers messages du noyau, sans le bruit réseau de Docker
  kernel)    { dmesg -T 2>/dev/null || journalctl -k -n 400 --no-pager 2>/dev/null; } | grep -vE "$KERNEL_NOISE" | tail -n 50 ;;
  # 100 dernières lignes du journal système, sans les connexions de Sentinel
  syslog)    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
             if [ -r /var/log/syslog ]; then tail -n 400 /var/log/syslog > "$TMP"
             elif has_systemd; then journalctl -n 400 --no-pager -o short-iso > "$TMP"
             else echo "journal système illisible"; exit 0; fi
             drop_own_ssh "$TMP" | tail -n 100 ;;
  # État de l'array Unraid et de ses disques, sinon RAID logiciel ou ZFS
  array)     if [ -f /var/local/emhttp/var.ini ]; then
               echo "== Array Unraid"; grep -E '^(mdState|fsState|mdResync|mdResyncAction|mdResyncPos|mdNumDisabled|mdNumInvalid|mdNumMissing|sbSynced|sbSyncErrs)=' /var/local/emhttp/var.ini
               echo; echo "== Disques (nom statut erreurs température système de fichiers)"
               awk -F= '/^\[/{if(n!="")print n, s, e, t, f; n=$0; s=e=t=f="-"} /^status=/{s=$2} /^numErrors=/{e=$2} /^temp=/{t=$2} /^fsStatus=/{f=$2} END{print n, s, e, t, f}' /var/local/emhttp/disks.ini \
                 | tr -d '"[]' | awk '$2 != "DISK_NP"'
             else
               [ -r /proc/mdstat ] && { echo "== RAID logiciel (/proc/mdstat)"; cat /proc/mdstat; }
               command -v zpool >/dev/null 2>&1 && { echo; echo "== ZFS"; zpool status -x 2>&1 | head -n 40; }
               [ -r /proc/mdstat ] || command -v zpool >/dev/null 2>&1 || echo "Aucun array, RAID logiciel ou pool ZFS détecté"
             fi ;;
  # Services systemd en échec
  failed-services) if has_systemd; then systemctl --failed --no-pager --plain; else no_systemd; fi ;;
  # État d'un service précis
  service)   valid_unit "$ARG" || { echo "nom de service invalide"; exit 2; }
             if has_systemd; then systemctl status --no-pager -n 20 -- "$ARG"; else no_systemd; fi ;;
  # 100 dernières lignes du journal d'un service précis
  journal)   valid_unit "$ARG" || { echo "nom de service invalide"; exit 2; }
             if has_systemd; then journalctl -u "$ARG" -n 100 --no-pager -o short-iso; else no_systemd; fi ;;
  # Montages déclarés au démarrage (/etc/fstab), mots de passe masqués, et état des unités de montage systemd
  fstab)     if [ -r /etc/fstab ]; then
               grep -vE '^[[:space:]]*(#|$)' /etc/fstab | sed -E 's/((pass(word)?|credentials|user(name)?)=)[^,[:space:]]+/\1***/g'
             else echo "/etc/fstab illisible"; fi
             if has_systemd; then echo; echo "== Unités de montage"
               systemctl list-units --type=mount --all --no-pager --plain --no-legend 2>/dev/null | grep -vE '^(-|sys-|proc-|dev-|run-|tmp\.mount|boot)' | head -n 30
             fi ;;
  # Partages exportés par un serveur NFS dont cette machine monte déjà quelque chose
  nfs-exports) known_nfs_server "$ARG" || { echo "serveur NFS refusé : seulement un serveur cité dans /etc/fstab ou déjà monté"; exit 2; }
             if command -v showmount >/dev/null 2>&1; then timeout 10 showmount -e -- "$ARG" 2>&1 | head -n 40
             else echo "showmount indisponible sur ce serveur (paquet nfs-common ou nfs-utils)"; fi ;;
  # Version de ce script (Sentinel signale quand il faut le recoller)
  # Version de ce script et adresse d'où Sentinel se connecte (pour limiter la clé à cette adresse : option from=)
  version)   echo "sentinel-diag $SCRIPT_VERSION client ${CLIENT_IP:-?}" ;;
  *)         echo "Contrôles disponibles : overview disk memory load processes mounts network kernel syslog array failed-services service <nom> journal <nom> fstab nfs-exports <serveur>" ;;
esac
'''


def _script() -> str:
    return DIAG_SCRIPT.replace("@VERSION@", DIAG_SCRIPT_VERSION)


def installed_script_info(cfg: dict) -> dict:
    """Version du script installé (None si ancien script sans numéro) et adresse de Sentinel vue par le serveur
    (depuis la version 4 ; None avant)."""
    out = run_host_check(cfg, "overview", _raw="version")
    m = re.match(r"^sentinel-diag (\S+)(?: client (\S+))?$", out.strip())
    if not m:
        return {"version": None, "client_ip": None}
    ip = m.group(2)
    try:
        ip = str(ipaddress.ip_address(ip)) if ip else None
    except ValueError:
        ip = None
    return {"version": m.group(1), "client_ip": ip}


def installed_script_version(cfg: dict) -> Optional[str]:
    return installed_script_info(cfg)["version"]


def check_source(source: Optional[str]) -> Optional[str]:
    """Adresses autorisées à utiliser la clé (option from= d'authorized_keys) : adresses IP ou réseaux CIDR, 4 au plus,
    séparés par des virgules ou des espaces. Vide : pas de limite. Une clé copiée hors de Sentinel ne sert alors à rien
    depuis une autre machine."""
    parts = [p for p in re.split(r"[,\s]+", (source or "").strip()) if p]
    if not parts:
        return None
    if len(parts) > 4:
        raise DiagnosticError("4 adresses au plus pour limiter la clé")
    out = []
    for p in parts:
        try:
            out.append(str(ipaddress.ip_network(p, strict=False)) if "/" in p else str(ipaddress.ip_address(p)))
        except ValueError:
            raise DiagnosticError(f"adresse invalide pour limiter la clé : {p[:60]}")
    return ",".join(out)


def _key_options(command: str, source: Optional[str]) -> str:
    src = check_source(source)
    return f'restrict,from="{src}",command="{command}"' if src else f'restrict,command="{command}"'


DEFAULT_ACCOUNT = "sentinel-diag"
ACCOUNT_COMMENT = "Log-to-LLM Sentinel"
_ACCOUNT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def check_account(name: str) -> str:
    """Name of the Linux account the install block creates: a new account, never root."""
    name = (name or "").strip()
    if not _ACCOUNT_RE.match(name) or name == "root":
        raise DiagnosticError("nom de compte invalide : minuscules, chiffres, - ou _, 32 caractères au plus, pas root")
    return name


def _guarded(account: str, body: str, refusal: str) -> str:
    """Runs body only on an account Sentinel created; the default name keeps working for older installs."""
    if account == DEFAULT_ACCOUNT:
        return body
    return (
        f"\n# 0. Vérification : le compte {account} doit avoir été créé par Sentinel\n"
        f"if id -u {account} >/dev/null 2>&1 && [ \"$(getent passwd {account} | cut -d: -f5)\" != '{ACCOUNT_COMMENT}' ]; then\n"
        f"  echo \"{refusal}\"\n"
        "else\n"
        f"{body}"
        "fi\n"
    )


def install_snippet(platform: str, account: str = DEFAULT_ACCOUNT, source: Optional[str] = None) -> str:
    """Bloc à coller dans un terminal root du serveur (Linux classique ou Unraid).

    Les lignes qui modifient le serveur viennent en tête, sous des titres numérotés ;
    le script de contrôles, plus long, vient en dernier pour que le bloc se relise facilement.
    """
    pub = public_key()
    if not pub:
        raise DiagnosticError("générer d'abord la clé")
    rule = "# " + "═" * 78 + "\n"
    if platform == "unraid":
        line = f'{_key_options("bash /boot/config/sentinel/sentinel-diag.sh", source)} {pub}'
        return (
            rule
            + "# Log-to-LLM Sentinel — diagnostic serveur pour Unraid (6.9 ou plus récent)\n"
            "# À coller dans le terminal d'Unraid (root). Ce bloc :\n"
            "#  1. autorise la clé de Sentinel à lancer UNIQUEMENT le script de contrôles,\n"
            "#  2. installe ce script, en lecture seule, sur la clé USB d'Unraid.\n"
            "# Seules les étapes 1 et 2 modifient le serveur ; la suite est le contenu du script.\n"
            + rule
            + "\n# 1. Clé de Sentinel, limitée au script (option restrict,command=)\n"
            "#    sur la clé USB (restaurée par Unraid à chaque démarrage)…\n"
            "mkdir -p /boot/config/sentinel /boot/config/ssh/root\n"
            "touch /boot/config/ssh/root/authorized_keys\n"
            "sed -i '/sentinel-diag/d' /boot/config/ssh/root/authorized_keys\n"
            f"echo '{line}' >> /boot/config/ssh/root/authorized_keys\n"
            "#    … et pour la session en cours\n"
            "mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch /root/.ssh/authorized_keys\n"
            "if [ ! /root/.ssh/authorized_keys -ef /boot/config/ssh/root/authorized_keys ]; then\n"
            "  sed -i '/sentinel-diag/d' /root/.ssh/authorized_keys\n"
            f"  echo '{line}' >> /root/.ssh/authorized_keys\n"
            "fi\n"
            "chmod 600 /root/.ssh/authorized_keys\n"
            "\n# 2. Script de contrôles : tout ce qui suit, jusqu'à SENTINEL_SCRIPT\n"
            "cat > /boot/config/sentinel/sentinel-diag.sh <<'SENTINEL_SCRIPT'\n"
            f"{_script()}SENTINEL_SCRIPT\n"
            "\necho 'Sentinel : diagnostic installé. Utilisateur à renseigner : root'\n"
        )
    u = check_account(account)
    line = f'{_key_options("/usr/local/bin/sentinel-diag", source)} {pub}'
    body = (
        "\n# 1. Utilisateur dédié, autorisé à lire les journaux système\n"
        f"sudo useradd --system --create-home --shell /bin/bash --comment '{ACCOUNT_COMMENT}' {u} 2>/dev/null || true\n"
        f"sudo usermod -aG systemd-journal,adm {u} 2>/dev/null || true\n"
        "\n# 2. Clé de Sentinel, limitée au script (option restrict,command=)\n"
        f"sudo install -d -m 700 -o {u} -g {u} /home/{u}/.ssh\n"
        f"echo '{line}' | sudo tee /home/{u}/.ssh/authorized_keys >/dev/null\n"
        f"sudo chown {u}: /home/{u}/.ssh/authorized_keys\n"
        f"sudo chmod 600 /home/{u}/.ssh/authorized_keys\n"
        "\n# 3. Script de contrôles : tout ce qui suit, jusqu'à SENTINEL_SCRIPT\n"
        "sudo tee /usr/local/bin/sentinel-diag >/dev/null <<'SENTINEL_SCRIPT'\n"
        f"{_script()}SENTINEL_SCRIPT\n"
        "sudo chmod 755 /usr/local/bin/sentinel-diag\n"
        f"\necho 'Sentinel : diagnostic installé. Utilisateur à renseigner : {u}'\n"
    )
    return (
        rule
        + "# Log-to-LLM Sentinel — diagnostic serveur pour Linux (Debian, Ubuntu…)\n"
        "# À coller dans un terminal du serveur. Ce bloc :\n"
        f"#  1. crée l'utilisateur système {u} (sans mot de passe ni sudo),\n"
        "#  2. autorise la clé de Sentinel à lancer UNIQUEMENT le script de contrôles,\n"
        "#  3. installe ce script, en lecture seule : /usr/local/bin/sentinel-diag.\n"
        "# Seules les étapes 1 à 3 modifient le serveur ; la suite est le contenu du script.\n"
        + rule
        + _guarded(u, body, f"Sentinel : le compte {u} existe déjà et n'a pas été créé par Sentinel. Rien n'a été modifié : choisis un autre nom dans Sentinel.")
    )


def uninstall_snippet(platform: str, account: str = DEFAULT_ACCOUNT) -> str:
    """Bloc qui défait exactement ce qu'a installé install_snippet (ne dépend pas de la clé)."""
    rule = "# " + "═" * 78 + "\n"
    if platform == "unraid":
        return (
            rule
            + "# Log-to-LLM Sentinel — retrait du diagnostic serveur pour Unraid\n"
            "# À coller dans le terminal d'Unraid (root). Retire la clé de Sentinel et son script.\n"
            + rule
            + "# 1. Clé de Sentinel : retirée de la clé USB et de la session en cours\n"
            "[ -f /boot/config/ssh/root/authorized_keys ] && sed -i '/sentinel-diag/d' /boot/config/ssh/root/authorized_keys\n"
            "if [ -f /root/.ssh/authorized_keys ] && [ ! /root/.ssh/authorized_keys -ef /boot/config/ssh/root/authorized_keys ]; then\n"
            "  sed -i '/sentinel-diag/d' /root/.ssh/authorized_keys\n"
            "fi\n"
            "# 2. Script de contrôles\n"
            "rm -f /boot/config/sentinel/sentinel-diag.sh\n"
            "rmdir /boot/config/sentinel 2>/dev/null\n"
            "echo 'Sentinel : accès retiré.'\n"
        )
    u = check_account(account)
    body = (
        "# 1. Clé de Sentinel (retirée en premier : plus aucune connexion possible)\n"
        f"sudo rm -f /home/{u}/.ssh/authorized_keys\n"
        "# 2. Script de contrôles\n"
        "sudo rm -f /usr/local/bin/sentinel-diag\n"
        "# 3. Utilisateur dédié et son dossier personnel\n"
        f"sudo userdel -r {u} 2>/dev/null || true\n"
        "echo 'Sentinel : accès retiré.'\n"
    )
    return (
        rule
        + "# Log-to-LLM Sentinel — retrait du diagnostic serveur pour Linux\n"
        "# À coller dans un terminal du serveur. Retire la clé de Sentinel, son script et son utilisateur.\n"
        + rule
        + _guarded(u, body, f"Sentinel : le compte {u} n'a pas été créé par Sentinel. Rien n'a été retiré.")
    )


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
