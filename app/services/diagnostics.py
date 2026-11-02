"""
Diagnostic en lecture seule de l'infrastructure, pour le chat et l'investigation :

- Docker : via un proxy du socket Docker qui n'autorise que les requêtes de lecture
  (ex. tecnativa/docker-socket-proxy avec CONTAINERS=1, INFO=1, POST=0). Sentinel ne monte jamais le socket.
- Serveur : via SSH, avec une clé générée par Sentinel et limitée côté serveur à un script
  (option `command=` d'authorized_keys). Sentinel ne transmet qu'un nom de contrôle d'une liste fermée.

Les sorties sont bornées et les variables d'environnement des conteneurs ne sont jamais renvoyées.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from app.database import SessionLocal
from app.models import GlobalConfig

DOCKER_TIMEOUT_S = 8.0
SSH_TIMEOUT_S = 20
MAX_LOG_LINES = 200
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,80}$")

# Contrôles serveur : nom → prend un argument (nom de service). Doit correspondre au script installé sur le serveur.
HOST_CHECKS = {
    "overview": False, "disk": False, "memory": False, "load": False, "processes": False,
    "mounts": False, "network": False, "kernel": False, "syslog": False, "array": False,
    "failed-services": False, "service": True, "journal": True,
}


class DiagnosticError(Exception):
    """Erreur présentable à l'utilisateur ou au LLM."""


# ── Réglages ────────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data"))


def ssh_dir() -> Path:
    return _data_dir() / "ssh"


def key_path() -> Path:
    return ssh_dir() / "sentinel_diag_ed25519"


def settings() -> dict:
    db = SessionLocal()
    try:
        c = db.query(GlobalConfig).first()
        return {
            "docker_proxy_url": ((c.docker_proxy_url if c else "") or "").strip().rstrip("/"),
            "ssh_host": ((c.ssh_host if c else "") or "").strip(),
            "ssh_port": (c.ssh_port if c else None) or 22,
            "ssh_user": ((c.ssh_user if c else "") or "").strip(),
        }
    finally:
        db.close()


def availability(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or settings()
    return {
        "docker": bool(cfg["docker_proxy_url"]),
        "host": bool(cfg["ssh_host"] and cfg["ssh_user"] and key_path().exists()),
    }


# ── Docker (proxy en lecture seule) ─────────────────────────────────────────────

def _docker_get(path: str, params: dict = None, base: str = None, raw: bool = False):
    base = base or settings()["docker_proxy_url"]
    if not base:
        raise DiagnosticError("diagnostic Docker non configuré")
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
    info = _docker_get("/info", base=base.strip().rstrip("/"))
    return {"server_version": info.get("ServerVersion"), "containers": info.get("Containers"),
            "running": info.get("ContainersRunning"), "name": info.get("Name")}


def docker_containers(all_containers: bool = True) -> list:
    rows = _docker_get("/containers/json", {"all": "true" if all_containers else "false"})
    return [{
        "name": (c.get("Names") or ["?"])[0].lstrip("/"),
        "image": c.get("Image"),
        "state": c.get("State"),
        "status": c.get("Status"),  # ex. "Up 3 hours (healthy)", "Restarting (1) 5 seconds ago"
    } for c in rows]


def docker_container_status(name: str) -> dict:
    d = _docker_get(f"/containers/{_check_container(name)}/json")
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


def docker_container_logs(name: str, lines: int = 100, since_minutes: Optional[int] = None) -> str:
    lines = max(1, min(int(lines or 100), MAX_LOG_LINES))
    params = {"stdout": "true", "stderr": "true", "timestamps": "true", "tail": str(lines)}
    if since_minutes:
        params["since"] = str(int(datetime.utcnow().timestamp()) - int(since_minutes) * 60)
    return _demux_logs(_docker_get(f"/containers/{_check_container(name)}/logs", params, raw=True)).strip()


def docker_container_stats(name: str) -> dict:
    s = _docker_get(f"/containers/{_check_container(name)}/stats", {"stream": "false"})
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


def run_host_check(check: str, arg: Optional[str] = None, cfg: Optional[dict] = None, _raw: Optional[str] = None) -> str:
    cfg = cfg or settings()
    if not availability(cfg)["host"]:
        raise DiagnosticError("diagnostic serveur non configuré")
    if check not in HOST_CHECKS:
        raise DiagnosticError(f"contrôle inconnu « {check} » (disponibles : {', '.join(HOST_CHECKS)})")
    words = [_raw] if _raw else [check]
    if HOST_CHECKS[check] and not _raw:
        if not arg or not UNIT_RE.match(arg):
            raise DiagnosticError("nom de service invalide")
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


def host_fingerprint() -> Optional[str]:
    kh = ssh_dir() / "known_hosts"
    if not kh.exists() or not shutil.which("ssh-keygen"):
        return None
    res = subprocess.run(["ssh-keygen", "-l", "-f", str(kh)], capture_output=True, timeout=10)
    return res.stdout.decode("utf-8", errors="replace").strip().splitlines()[0] if res.returncode == 0 and res.stdout else None


# ── Script installé sur le serveur ──────────────────────────────────────────────

DIAG_SCRIPT_VERSION = "2"  # à incrémenter à chaque modification du script : Sentinel signale un script à mettre à jour

DIAG_SCRIPT = r'''#!/bin/bash
# Log-to-LLM Sentinel — diagnostic en lecture seule.
# Appelé uniquement par la clé SSH de Sentinel (option command= dans authorized_keys) :
# seuls les contrôles ci-dessous sont possibles, aucune autre commande n'est exécutée.
set -u
SCRIPT_VERSION=@VERSION@
read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-help}"
CHECK="${ARGS[0]:-help}"
ARG="${ARGS[1]:-}"
CLIENT_IP="${SSH_CONNECTION%% *}"
valid_unit() { [[ "$1" =~ ^[A-Za-z0-9@._:-]{1,80}$ ]]; }
has_systemd() { command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }
no_systemd() {
  if [ -f /etc/unraid-version ]; then echo "Unraid n'utilise pas systemd : utiliser les contrôles syslog, array, processes ou les outils Docker."
  else echo "systemd indisponible sur ce système"; fi
}
# Retire les lignes dues aux connexions de Sentinel lui-même (sessions SSH venant de son adresse)
drop_own_ssh() {
  [ -n "$CLIENT_IP" ] || { cat "$1"; return; }
  local pids
  pids=$(grep -F "$CLIENT_IP" "$1" 2>/dev/null | grep -oE 'sshd(-session)?\[[0-9]+\]' | grep -oE '[0-9]+' | sort -u | paste -sd'|')
  if [ -n "$pids" ]; then grep -vE "sshd(-session)?\[($pids)\]" "$1"; else cat "$1"; fi
}
DF_EXCL="-x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs"
case "$CHECK" in
  overview)  uptime; echo; free -m; echo; df -hT $DF_EXCL 2>/dev/null ;;
  disk)      df -hT $DF_EXCL 2>/dev/null; echo; df -i $DF_EXCL 2>/dev/null | head -n 20 ;;
  memory)    free -m; echo; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|Dirty|Committed_AS)' /proc/meminfo ;;
  load)      uptime; echo "CPU : $(nproc)"; cat /proc/loadavg ;;
  processes) ps -eo pid,ppid,user,%cpu,%mem,etime,comm --sort=-%cpu | awk -v me=$$ 'NR==1 || ($1!=me && $2!=me && $7!~/^sshd/)' | cut -c1-120 | head -n 16
             echo; ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -n 11 ;;
  mounts)    (findmnt -rn -t nfs,nfs4,cifs,smb3,fuse.sshfs,fuse.shfs -o TARGET,SOURCE,FSTYPE 2>/dev/null) || mount | grep -E ' (nfs|nfs4|cifs|smb3|fuse\.[a-z]+) ' ;;
  network)   ip -br addr 2>/dev/null | grep -vE '^(veth|br-[0-9a-f]{12}|docker0|vhost)' ; echo; ip route 2>/dev/null | grep -vE 'br-[0-9a-f]{12}|docker0' | head -n 20 ;;
  kernel)    { dmesg -T 2>/dev/null || journalctl -k -n 400 --no-pager 2>/dev/null; } \
               | grep -vE 'veth[0-9a-f]+|br-[0-9a-f]{12}|docker0|entered (blocking|forwarding|disabled) state|(allmulticast|promiscuous) mode|renamed from veth|elogind.*(New|Removed) session' | tail -n 50 ;;
  syslog)    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
             if [ -r /var/log/syslog ]; then tail -n 400 /var/log/syslog > "$TMP"
             elif has_systemd; then journalctl -n 400 --no-pager -o short-iso > "$TMP"
             else echo "journal système illisible"; exit 0; fi
             drop_own_ssh "$TMP" | tail -n 100 ;;
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
  failed-services) if has_systemd; then systemctl --failed --no-pager --plain; else no_systemd; fi ;;
  service)   valid_unit "$ARG" || { echo "nom de service invalide"; exit 2; }
             if has_systemd; then systemctl status --no-pager -n 20 -- "$ARG"; else no_systemd; fi ;;
  journal)   valid_unit "$ARG" || { echo "nom de service invalide"; exit 2; }
             if has_systemd; then journalctl -u "$ARG" -n 100 --no-pager -o short-iso; else no_systemd; fi ;;
  version)   echo "sentinel-diag $SCRIPT_VERSION" ;;
  *)         echo "Contrôles disponibles : overview disk memory load processes mounts network kernel syslog array failed-services service <nom> journal <nom>" ;;
esac
'''


def _script() -> str:
    return DIAG_SCRIPT.replace("@VERSION@", DIAG_SCRIPT_VERSION)


def installed_script_version(cfg: Optional[dict] = None) -> Optional[str]:
    """Version du script installé sur le serveur (None si ancien script sans numéro de version)."""
    out = run_host_check("overview", cfg=cfg, _raw="version")
    m = re.match(r"^sentinel-diag (\S+)$", out.strip())
    return m.group(1) if m else None


def install_snippet(platform: str) -> str:
    """Bloc à coller dans un terminal root du serveur (Linux classique ou Unraid)."""
    pub = public_key()
    if not pub:
        raise DiagnosticError("générer d'abord la clé")
    if platform == "unraid":
        line = f'restrict,command="bash /boot/config/sentinel/sentinel-diag.sh" {pub}'
        return (
            "# Unraid (6.9 ou plus récent) — à coller dans le terminal d'Unraid (root)\n"
            "mkdir -p /boot/config/sentinel /boot/config/ssh/root\n"
            "cat > /boot/config/sentinel/sentinel-diag.sh <<'SENTINEL_SCRIPT'\n"
            f"{_script()}SENTINEL_SCRIPT\n"
            "touch /boot/config/ssh/root/authorized_keys\n"
            "sed -i '/sentinel-diag/d' /boot/config/ssh/root/authorized_keys\n"
            f"echo '{line}' >> /boot/config/ssh/root/authorized_keys\n"
            "# Session en cours (la copie sur la clé USB est restaurée par Unraid au démarrage)\n"
            "mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch /root/.ssh/authorized_keys\n"
            "if [ ! /root/.ssh/authorized_keys -ef /boot/config/ssh/root/authorized_keys ]; then\n"
            "  sed -i '/sentinel-diag/d' /root/.ssh/authorized_keys\n"
            f"  echo '{line}' >> /root/.ssh/authorized_keys\n"
            "fi\n"
            "chmod 600 /root/.ssh/authorized_keys\n"
            "echo 'Sentinel : diagnostic installé. Utilisateur à renseigner : root'\n"
        )
    line = f'restrict,command="/usr/local/bin/sentinel-diag" {pub}'
    return (
        "# Linux (Debian, Ubuntu…) — à coller dans un terminal du serveur\n"
        "sudo useradd --system --create-home --shell /bin/bash sentinel-diag 2>/dev/null || true\n"
        "sudo usermod -aG systemd-journal,adm sentinel-diag 2>/dev/null || true\n"
        "sudo tee /usr/local/bin/sentinel-diag >/dev/null <<'SENTINEL_SCRIPT'\n"
        f"{_script()}SENTINEL_SCRIPT\n"
        "sudo chmod 755 /usr/local/bin/sentinel-diag\n"
        "sudo install -d -m 700 -o sentinel-diag -g sentinel-diag /home/sentinel-diag/.ssh\n"
        f"echo '{line}' | sudo tee /home/sentinel-diag/.ssh/authorized_keys >/dev/null\n"
        "sudo chown sentinel-diag: /home/sentinel-diag/.ssh/authorized_keys\n"
        "sudo chmod 600 /home/sentinel-diag/.ssh/authorized_keys\n"
        "echo 'Sentinel : diagnostic installé. Utilisateur à renseigner : sentinel-diag'\n"
    )


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
