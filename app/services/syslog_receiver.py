import asyncio
import logging
import os
import re
import socket
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Rule, GlobalConfig

logger = logging.getLogger(__name__)

# Pattern RFC 3164 : <PRI>MMM DD HH:MM:SS hostname process[pid]: message
# Parfois le pid ou le process ou la PRI manque, nous faisons un regex tolérant
SYSLOG_REGEX = re.compile(r"^(?:<(\d+)>)?([A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+([^\s]+)\s+(.*)$")

_orchestrator = None
_BUFFER_MAX = 2000
_syslog_buffers: Dict[str, deque] = {}
_SYSLOG_LOG_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "syslog"

def set_orchestrator(orchestrator_instance):
    global _orchestrator
    _orchestrator = orchestrator_instance

def _log_path(hostname: str) -> Path:
    safe = "".join(c for c in hostname if c.isalnum() or c in "-_")
    return _SYSLOG_LOG_DIR / f"{safe}.log"

def _get_buffer(hostname: str) -> deque:
    if hostname not in _syslog_buffers:
        buf = deque(maxlen=_BUFFER_MAX)
        fp = _log_path(hostname)
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        buf.append(line.rstrip("\n"))
            except Exception:
                pass
        _syslog_buffers[hostname] = buf
    return _syslog_buffers[hostname]

def _append_to_disk(hostname: str, lines: list[str]):
    fp = _log_path(hostname)
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        _maybe_truncate(fp)
    except Exception as e:
        logger.warning(f"Syslog log write error: {e}")

def _maybe_truncate(fp: Path):
    try:
        stat = fp.stat()
        if stat.st_size < 100_000:
            return
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        if len(all_lines) > _BUFFER_MAX:
            with open(fp, "w", encoding="utf-8") as f:
                f.writelines(all_lines[-_BUFFER_MAX:])
    except Exception:
        pass


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, receiver: "SyslogReceiver"):
        self.receiver = receiver
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        # 1. Forward raw datagram to relay if configured
        if self.receiver.forward_addr:
            try:
                self.transport.sendto(data, self.receiver.forward_addr)
            except Exception as e:
                logger.error(f"Error forwarding syslog datagram to {self.receiver.forward_addr}: {e}")

        # 2. Process locally
        try:
            text = data.decode("utf-8", errors="ignore").strip()
            if not text:
                return
            self.receiver.process_raw_line(text)
        except Exception as e:
            logger.error(f"Error parsing datagram from {addr}: {e}")


class SyslogReceiver:
    def __init__(self):
        self.transport = None
        self.protocol = None
        self.enabled = False
        self.forward_addr = None # Tuple (IP, port)
        self.active_rules: Dict[str, Rule] = {} # hostname -> rule

    def load_config(self):
        """Load configuration from database."""
        db = SessionLocal()
        try:
            config = db.query(GlobalConfig).first()
            if config:
                self.enabled = bool(config.syslog_enabled)
                self.forward_addr = None
                if config.syslog_forward_addr:
                    addr_parts = config.syslog_forward_addr.strip().split(":", 1)
                    ip = addr_parts[0]
                    port = 514
                    if len(addr_parts) > 1 and addr_parts[1].isdigit():
                        port = int(addr_parts[1])
                    self.forward_addr = (ip, port)
            else:
                self.enabled = False
                self.forward_addr = None

            # Load active syslog rules
            self.active_rules = {}
            rules = db.query(Rule).filter(Rule.enabled == True, Rule.log_file_path.like("[SYSLOG]:%")).all()
            for r in rules:
                hostname = r.log_file_path.split(":", 1)[1].strip()
                if hostname:
                    self.active_rules[hostname] = r
            logger.info(f"Loaded {len(self.active_rules)} active syslog rules. Enabled={self.enabled}, Forward={self.forward_addr}")
        except Exception as e:
            logger.error(f"Error loading SyslogReceiver config: {e}")
        finally:
            db.close()

    async def start(self):
        """Start listening on UDP port 514."""
        self.load_config()
        if not self.enabled:
            logger.info("Syslog receiver disabled in global configuration.")
            return

        loop = asyncio.get_running_loop()
        try:
            # We listen on port 514 inside the container
            self.transport, self.protocol = await loop.create_datagram_endpoint(
                lambda: SyslogProtocol(self),
                local_addr=("0.0.0.0", 514)
            )
            logger.info("Syslog receiver UDP listening on port 514")
        except Exception as e:
            logger.error(f"Failed to start Syslog receiver: {e}")

    async def stop(self):
        """Stop listening."""
        if self.transport:
            self.transport.close()
            self.transport = None
            self.protocol = None
            logger.info("Syslog receiver stopped.")

    async def reload(self):
        """Reload configuration and restart if needed."""
        await self.stop()
        await self.start()

    def process_raw_line(self, line: str):
        # Match RFC 3164
        match = SYSLOG_REGEX.match(line)
        if match:
            pri, timestamp, hostname, message = match.groups()
        else:
            # Fallback if unparseable
            hostname = "unknown"
            message = line

        # Clean/sanitize hostname
        hostname = hostname.strip()

        # Check if there is an active rule for this hostname
        rule = self.active_rules.get(hostname)
        if not rule:
            # Also support a catch-all rules or wildcards if user has "*" rule
            rule = self.active_rules.get("*")

        # Prepare stamped message
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        stamped = f"{ts}  {line}"

        # 1. Always append to host-specific buffer and disk
        buf = _get_buffer(hostname)
        buf.append(stamped)
        _append_to_disk(hostname, [stamped])

        # 2. Always append to global catch-all (*) buffer and disk
        if hostname != "*":
            global_buf = _get_buffer("*")
            global_buf.append(stamped)
            _append_to_disk("*", [stamped])

        if rule:
            # Trigger orchestrator
            if _orchestrator:
                db = SessionLocal()
                try:
                    # Refresh rule object inside this DB session thread
                    db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
                    if db_rule and db_rule.enabled:
                        db_rule.last_line_received_at = datetime.utcnow()
                        db_rule.inactivity_notified = False
                        db.commit()

                        # Ingest line into AI pipeline
                        # datagram_received is an asyncio-native callback (NOT a separate OS thread),
                        # so run_coroutine_threadsafe + get_event_loop() is wrong here and fails
                        # silently on Python 3.10+. Use ensure_future to schedule on the running loop,
                        # exactly like log_watcher.py does with `await on_new_lines(...)`.
                        asyncio.ensure_future(
                            _orchestrator.handle_new_lines(rule, [stamped])
                        )
                except Exception as ex:
                    logger.error(f"Error executing orchestrator check for syslog line: {ex}")
                finally:
                    db.close()


# Global instance
syslog_receiver = SyslogReceiver()
