"""
Keyword Auto-Learning Service
Scans a log file over a user-defined period split into time-window packets.
Each packet is sent to the LLM within the max_log_chars budget.
Phase 1: per-packet keyword discovery
Phase 2: global refinement of the union of candidates
Then: immediate auto-validation + notification at each major step.
"""
import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.database import SessionLocal
from app.models import Rule, GlobalConfig, KeywordLearningSession
from app import logger as app_logger

# ── Timestamp patterns (priority order) ───────────────────────────────────────
_TS_PATTERNS = [
    # ISO 8601:  2026-04-26T19:23:02  or  2026-04-26 19:23:02
    (re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})'), '%Y-%m-%dT%H:%M:%S'),
    # Syslog / journald:  Apr 26 19:23:02
    (re.compile(r'([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})'), '%b %d %H:%M:%S'),
    # Nginx/Apache:  26/Apr/2026:19:23:02
    (re.compile(r'(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2})'), '%d/%b/%Y:%H:%M:%S'),
]

_CURRENT_YEAR = datetime.utcnow().year


def _resolve_log_path(log_path: str) -> str:
    """Resolve a log_file_path to a physical file path.

    For webhook rules the path is stored as ``[WEBHOOK]:<token>`` in the DB.
    The webhook router persists received lines to ``data/webhooks/<token>.log``.
    This helper returns the physical path so the learning service can read it
    like any other log file.
    """
    if log_path and log_path.startswith('[WEBHOOK]:'):
        token = log_path.split(':', 1)[1]
        safe = "".join(c for c in token if c.isalnum() or c in "-_")
        webhook_dir = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "webhooks"
        return str(webhook_dir / f"{safe}.log")
    return log_path

# ── Prompts ────────────────────────────────────────────────────────────────────
_PROMPT_PHASE1 = """\
You are a system log monitoring expert.
The user monitors this file to be alerted ONLY on events requiring real attention:
failures, critical errors, performance degradation, security issues.

Analyzed time window: {window}

Analyze these log lines and return ONLY a JSON list of SHORT keywords to detect anomalies.

STRICT RULES:
- Maximum 15 keywords
- Each keyword: 1 to 3 words MAX (no full sentences)
- Keywords must be GENERIC: they must match future occurrences of the same event type
- Remove variable numbers (counters, PIDs, timestamps, IDs)
- Avoid overly generic terms: info, log, time, started, stopping, message, repeated
- VALID examples: "restart counter", "job worker", "out of memory", "connection refused"
- INVALID examples: "restart counter is at 2361", "Background job will stop at 14:32"

Return ONLY raw JSON, no surrounding text.
Format: ["keyword1", "keyword2", ...]

--- LOG LINES ---
{lines}
"""

_PROMPT_PHASE2 = """\
You are a system log monitoring expert.
Below is a list of {n_raw} candidate keywords extracted from {n_packets} log time-windows:
{raw_keywords}

Representative log sample (beginning + end of file):
{sample}

Goal: monitor this system in production and alert the user ONLY on actionable events.

STRICT RULES — you MUST follow ALL of these:
1. SELECT keywords ONLY from the candidate list above. DO NOT invent new terms.
2. Each retained keyword: 1 to 3 words MAX. If a candidate is longer, shorten it to its essential part.
3. Remove noisy, overly generic, or redundant candidates.
4. Keep ONLY those signaling real, actionable problems visible in the logs.
5. Maximum 15 final keywords.

Respond ONLY with JSON:
{{"keywords": ["word1", "word2"], "rationale": {{"word1": "short reason"}}}}
"""

_PROMPT_VALIDATE = """\
You are a log monitoring expert reviewing a keyword refinement result.

Original candidate list ({n_raw} keywords extracted from real logs):
{raw_keywords}

Refined list proposed:
{refined_keywords}

Is the refined list relevant and faithful to the original candidates?
Answer with YES or NO only. No explanation.
"""

# Shufflable sections for retry (P_B and P_C will be randomly ordered)
_REPHRASE_SECTION_CONSTRAINTS = """\
STRICT CONSTRAINTS (mandatory):
- Select ONLY from the candidate list. DO NOT invent new terms.
- Each keyword: 1 to 3 words MAX. Shorten if longer.
- Remove generic noise. Keep actionable signals.
- Max 15 keywords total.
Respond ONLY with JSON: {{"keywords": [...], "rationale": {{...}}}}
"""

_REPHRASE_SECTION_CANDIDATES = """\
Section {idx}. Candidate keywords extracted from real log windows:
{raw_keywords}
"""

_REPHRASE_SECTION_SAMPLE = """\
Section {idx}. Representative log file excerpt:
{sample}
"""

_REPHRASE_SECTION_TASK = """\
Task: Refine the candidate list above into a final keyword set for production log monitoring.
Respond ONLY with JSON: {{"keywords": [...], "rationale": {{...}}}}
"""

_PROMPT_EXTRACT_PHRASES = """\
You are a log monitoring expert.
The following list contains some phrases that are too long to use as log keywords.
For each phrase, extract the 1-3 word core keyword that would actually match in a log line.
Keep entries that are already short (1-3 words) unchanged.

List to process:
{phrase_list}

Respond ONLY with a raw JSON array of short keywords (1-3 words each).
Format: ["keyword1", "keyword2", ...]
"""


def _parse_line_ts(line: str) -> Optional[datetime]:
    """Extract UTC-naive datetime from a log line. Returns None if unparseable."""
    for pattern, fmt in _TS_PATTERNS:
        m = pattern.search(line)
        if m:
            raw = m.group(1).replace(' ', 'T', 1)  # normalise syslog spaces
            try:
                dt = datetime.strptime(raw, fmt.replace(' ', 'T', 1))
                # Syslog has no year — assume current year
                if dt.year == 1900:
                    dt = dt.replace(year=_CURRENT_YEAR)
                return dt
            except ValueError:
                continue
    return None


def _extract_json_list(text: str) -> list:
    """Try to extract a JSON array from LLM response.
    Handles: direct JSON, markdown code blocks, embedded arrays anywhere in the text.
    """
    text = text.strip()

    # 1. Strip markdown code fences: ```json [...] ``` or ``` [...] ```
    code_block = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL | re.IGNORECASE)
    if code_block:
        try:
            val = json.loads(code_block.group(1))
            if isinstance(val, list):
                return [str(k) for k in val if k]
        except Exception:
            pass

    # 2. Direct parse (LLM returned ONLY the JSON)
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return [str(k) for k in val if k]
    except Exception:
        pass

    # 3. Find ALL [...] blocks and try each one
    #    Use greedy match to capture the largest possible array
    for m in re.finditer(r'(\[(?:[^\[\]]*|\[[^\[\]]*\])*\])', text, re.DOTALL):
        candidate = m.group(1).strip()
        try:
            val = json.loads(candidate)
            if isinstance(val, list) and val and isinstance(val[0], str):
                return [str(k) for k in val if isinstance(k, str) and k.strip()]
        except Exception:
            continue

    return []


def _extract_json_phase2(text: str) -> tuple[list, dict]:
    """Extract keywords list and rationale dict from phase-2 LLM response."""
    text = text.strip()
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            kws = val.get('keywords', [])
            rat = val.get('rationale', {})
            return ([str(k) for k in kws], {str(k): str(v) for k, v in rat.items()})
    except Exception:
        pass
    # Fallback: extract keywords array only
    kws = _extract_json_list(text)
    return kws, {}


def _read_window(path: str, t_start: datetime, t_end: datetime,
                 max_chars: int, has_timestamps: bool, fallback_offset: int) -> tuple[str, int]:
    """
    Read lines from path within [t_start, t_end).
    Returns (text_block, new_fallback_offset).
    If has_timestamps=False uses fallback_offset + char slicing.
    """
    lines_out = []
    chars = 0
    try:
        if has_timestamps:
            with open(path, 'r', errors='ignore') as f:
                for line in f:
                    stripped = line.rstrip()
                    if not stripped:
                        continue
                    ts = _parse_line_ts(stripped)
                    if ts is None:
                        continue
                    if ts < t_start:
                        continue
                    if ts >= t_end:
                        break  # logs are chronological — stop early
                    needed = len(stripped) + 1
                    if chars + needed > max_chars:
                        break
                    lines_out.append(stripped)
                    chars += needed
            return '\n'.join(lines_out), fallback_offset
        else:
            # Fallback: read max_chars starting at fallback_offset
            with open(path, 'r', errors='ignore') as f:
                f.seek(fallback_offset)
                chunk = f.read(max_chars)
                new_offset = f.tell()
            return chunk, new_offset
    except Exception as e:
        return f'[Erreur lecture: {e}]', fallback_offset


def _read_sample(path: str, max_chars: int) -> str:
    """Read a short representative sample (first 1/2 + last 1/2 of budget)."""
    half = max_chars // 2
    try:
        with open(path, 'r', errors='ignore') as f:
            start = f.read(half)
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            seek_pos = max(0, size - half)
            f.seek(seek_pos)
            end = f.read(half).decode('utf-8', errors='ignore')
        return (start + '\n...\n' + end)[:max_chars]
    except Exception:
        return ''


def _detect_timestamps(path: str, sample_lines: int = 50) -> bool:
    """Check if the log file has parseable timestamps in the first N lines."""
    try:
        with open(path, 'r', errors='ignore') as f:
            for i, line in enumerate(f):
                if i >= sample_lines:
                    break
                if _parse_line_ts(line.rstrip()):
                    return True
    except Exception:
        pass
    return False


# ── Session log file helpers ──────────────────────────────────────────────────

def _session_log_path(session_id: int) -> str:
    """Return path to the plaintext debug log for a learning session."""
    log_dir = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "learning_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / f"session_{session_id}.txt")


def _log_header(session_id: int, rule_id, log_path: str, n_packets: int,
                period_start, period_end):
    """Write the header block to the session log file."""
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write("=" * 56 + "\n")
            f.write(f"Sentinel — Auto-learning Session #{session_id}\n")
            f.write(f"Rule ID : {rule_id or 'N/A'}\n")
            f.write(f"Period  : {period_start.strftime('%Y-%m-%d %H:%M')} UTC"
                    f" → {period_end.strftime('%Y-%m-%d %H:%M')} UTC\n")
            f.write(f"Packets : {n_packets}\n")
            f.write(f"Started : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
            f.write("=" * 56 + "\n\n")
    except Exception:
        pass


def _log_exchange(log_path: str, phase: str, prompt: str,
                  response: str, decision: str = ""):
    """Append one Ollama exchange to the session log file."""
    ts = datetime.utcnow().strftime('%H:%M:%S')
    sep = "-" * 48
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] {phase}\n")
            if prompt:
                for line in prompt.splitlines():
                    f.write(f"  PROMPT | {line}\n")
            if response:
                for line in response.splitlines():
                    f.write(f"  RESP   | {line}\n")
            if decision:
                f.write(f"  => {decision}\n")
            f.write(sep + "\n\n")
    except Exception:
        pass


def _log_footer(log_path: str, success: bool, keywords: list,
                reason: str = ""):
    """Write the footer block to the session log file."""
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write("=" * 56 + "\n")
            if success:
                f.write(f"✅ SESSION VALIDATED — {len(keywords)} keyword(s) applied\n")
                f.write(f"   Keywords: {' | '.join(keywords)}\n")
            else:
                f.write(f"❌ SESSION CANCELLED\n")
                f.write(f"   Reason: {reason}\n")
            f.write(f"Ended: {ts} UTC\n")
            f.write("=" * 56 + "\n")
    except Exception:
        pass


# ── Phrase detection helpers ──────────────────────────────────────────────────

def _is_phrase(kw: str) -> bool:
    """Return True if kw is a phrase (> 3 words) rather than a short keyword."""
    return len(kw.split()) > 3


def _split_kws_and_phrases(keywords: list) -> tuple:
    """Split a keyword list into (short_keywords, long_phrases)."""
    short = [k for k in keywords if not _is_phrase(k)]
    phrases = [k for k in keywords if _is_phrase(k)]
    return short, phrases


# ── Validation pipeline async helpers ────────────────────────────────────────

async def _validate_refined_list(ollama, raw_kws: list, refined_kws: list,
                                  ollama_url: str, ollama_model: str,
                                  ollama_think: bool, ollama_temp: float,
                                  ollama_ctx: int, log_path: str,
                                  attempt: int) -> bool:
    """Ask Ollama YES/NO: is the refined list relevant to the raw candidates?"""
    prompt = _PROMPT_VALIDATE.format(
        n_raw=len(raw_kws),
        raw_keywords=json.dumps(raw_kws),
        refined_keywords=json.dumps(refined_kws),
    )
    try:
        resp = await asyncio.wait_for(
            ollama.analyze_async(
                prompt=prompt, url=ollama_url, model=ollama_model,
                think=ollama_think,
                options={'temperature': ollama_temp, 'num_ctx': ollama_ctx}
            ),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        resp = "NO"
    answer = resp.strip().upper()
    is_yes = answer.startswith("YES")
    _log_exchange(log_path,
                  f"VALIDATION — attempt {attempt}/3",
                  prompt, resp,
                  "✅ YES — list accepted" if is_yes else "❌ NO — will retry")
    return is_yes


async def _refine_with_shuffle(ollama, all_raw: list, sample: str,
                                ollama_url: str, ollama_model: str,
                                ollama_think: bool, ollama_temp: float,
                                ollama_ctx: int, log_path: str,
                                attempt: int) -> tuple:
    """Re-run phase 2 with shuffled paragraph order for a different perspective."""
    # Build shufflable sections
    section_b = _REPHRASE_SECTION_CANDIDATES.format(
        idx=0, raw_keywords=json.dumps(all_raw))
    section_c = _REPHRASE_SECTION_SAMPLE.format(
        idx=0, sample=sample[:2000])
    shuffled = [section_b, section_c]
    random.shuffle(shuffled)
    # Re-number after shuffle
    for i, _ in enumerate(shuffled):
        shuffled[i] = shuffled[i].replace("Section 0.", f"Section {i + 1}.", 1)

    prompt = (
        _REPHRASE_SECTION_CONSTRAINTS
        + "\n".join(shuffled)
        + _REPHRASE_SECTION_TASK
    )
    try:
        resp = await asyncio.wait_for(
            ollama.analyze_async(
                prompt=prompt, url=ollama_url, model=ollama_model,
                think=ollama_think,
                options={'temperature': min(ollama_temp + 0.1 * attempt, 0.7),
                         'num_ctx': ollama_ctx}
            ),
            timeout=180.0
        )
    except asyncio.TimeoutError:
        resp = json.dumps({'keywords': all_raw[:15], 'rationale': {}})

    kws, rationale = _extract_json_phase2(resp)
    kws = kws[:15]
    _log_exchange(log_path,
                  f"RETRY (shuffle #{attempt}) — PHASE 2 reformulation",
                  prompt, resp,
                  f"New candidate list: {kws}")
    return kws, rationale


async def _extract_keywords_from_phrases(ollama, phrases: list,
                                          real_kws: list,
                                          ollama_url: str, ollama_model: str,
                                          ollama_think: bool, ollama_temp: float,
                                          ollama_ctx: int,
                                          log_path: str) -> list:
    """Ask Ollama to extract short keywords from long phrases."""
    phrase_list = "\n".join(f"- {p}" for p in phrases)
    prompt = _PROMPT_EXTRACT_PHRASES.format(phrase_list=phrase_list)
    try:
        resp = await asyncio.wait_for(
            ollama.analyze_async(
                prompt=prompt, url=ollama_url, model=ollama_model,
                think=ollama_think,
                options={'temperature': ollama_temp, 'num_ctx': ollama_ctx}
            ),
            timeout=90.0
        )
    except asyncio.TimeoutError:
        resp = '[]'

    extracted = _extract_json_list(resp)
    # Keep only short results
    extracted = [k for k in extracted if not _is_phrase(k)]
    combined = list(dict.fromkeys(real_kws + extracted))[:15]
    _log_exchange(log_path,
                  "PHRASE EXTRACTION — converting phrases to keywords",
                  prompt, resp,
                  f"Extracted: {extracted} | Combined: {combined}")
    return combined


def _db_session_get_status(session_id: int) -> Optional[str]:
    """Return only the status string to avoid detached-object issues."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        return s.status if s else None
    finally:
        db.close()


def _db_update(session_id: int, **kwargs):
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if s:
            for k, v in kwargs.items():
                setattr(s, k, v)
            db.commit()
    finally:
        db.close()


async def _send_notification(rule_id: Optional[int], subject: str, body: str):
    """Send a notification via the existing NotificationService if the rule has notify_on_match."""
    if not rule_id:
        return
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule or not rule.notify_on_match:
            return
        cfg = db.query(GlobalConfig).first()
        if not cfg:
            return
        from app.routers.config import _get_config_dict
        config_dict = _get_config_dict(cfg)
    finally:
        db.close()

    from app.services.notification_service import NotificationService
    notifier = NotificationService()
    try:
        notifier.send(subject, body, config_dict)
    except Exception as e:
        app_logger.warning('KeywordLearning', f'Notification failed: {e}')


# ── Main session runner ───────────────────────────────────────────────────────

async def start_session(rule_id: Optional[int], log_path: str,
                        period_start: datetime, period_end: datetime,
                        granularity_s: int) -> int:
    """Create a KeywordLearningSession and launch the background task. Returns session_id."""
    db = SessionLocal()
    try:
        cfg = db.query(GlobalConfig).first()
        max_chars = cfg.max_log_chars if cfg else 5000

        # Save keywords before learning for potential revert
        prev_keywords = []
        if rule_id:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule:
                prev_keywords = rule.get_keywords()

        total_s = int((period_end - period_start).total_seconds())
        n_packets = max(1, (total_s + granularity_s - 1) // granularity_s)

        session = KeywordLearningSession(
            rule_id=rule_id,
            log_file_path=log_path,
            period_start=period_start,
            period_end=period_end,
            granularity_s=granularity_s,
            max_chars_per_packet=max_chars,
            status='pending',
            total_packets=n_packets,
            completed_packets=0,
            raw_keywords_json='[]',
            final_keywords_json='[]',
            previous_keywords_json=json.dumps(prev_keywords),
            refine_rationale_json='{}',
            ollama_log_json='[]',
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id

        # Mark rule as having a learning session
        if rule_id:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule:
                rule.last_learning_session_id = session_id
                db.commit()
    finally:
        db.close()

    # Fire and forget
    asyncio.create_task(_run_session(session_id))
    app_logger.info('KeywordLearning', f'Session {session_id} started for rule_id={rule_id}')
    return session_id


async def resume_stuck_sessions():
    """Resume learning sessions that were interrupted by a restart.

    Called once at application startup.  Sessions stuck in 'pending',
    'scanning' or 'refining' are re-launched — ``_run_session`` already
    handles resuming from the last completed packet.
    """
    db = SessionLocal()
    try:
        stuck = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.status.in_(['pending', 'scanning', 'refining'])
        ).all()
        if not stuck:
            return
        session_ids = [(s.id, s.rule_id, s.completed_packets, s.total_packets) for s in stuck]
    finally:
        db.close()

    for sid, rid, done, total in session_ids:
        app_logger.info(
            'KeywordLearning',
            f'Reprise automatique de la session {sid} (règle {rid}) — '
            f'{done}/{total} paquets déjà traités'
        )
        asyncio.create_task(_run_session(sid))


async def _run_session(session_id: int):
    """Main coroutine: phase 1 scan → phase 2 refine → validate."""
    try:
        db = SessionLocal()
        try:
            s = db.query(KeywordLearningSession).filter(
                KeywordLearningSession.id == session_id).first()
            if not s:
                return
            rule_id     = s.rule_id
            log_path    = _resolve_log_path(s.log_file_path)
            period_start = s.period_start
            period_end   = s.period_end
            granularity_s = s.granularity_s
            max_chars    = s.max_chars_per_packet
            n_packets    = s.total_packets
        finally:
            db.close()

        gran_label = (
            f"{granularity_s // 60} minute(s)" if granularity_s < 3600
            else f"{granularity_s // 3600} heure(s)" if granularity_s < 86400
            else f"{granularity_s // 86400} jour(s)"
        )

        # ── Init session log file ────────────────────────────────────────────
        sess_log = _session_log_path(session_id)
        _log_header(session_id, rule_id, sess_log, n_packets, period_start, period_end)

        # ── Restore accumulated state from DB (supports resume after restart) ──
        db = SessionLocal()
        try:
            s = db.query(KeywordLearningSession).filter(
                KeywordLearningSession.id == session_id).first()
            resume_from = s.completed_packets if s else 0
            all_raw = json.loads(s.raw_keywords_json or '[]') if s else []
            log_entries = json.loads(s.ollama_log_json or '[]') if s else []
        finally:
            db.close()

        first_packet_sent = bool(all_raw)  # skip first-packet notification on resume

        if resume_from > 0:
            app_logger.info(
                'KeywordLearning',
                f'Session {session_id} — reprise depuis le paquet {resume_from + 1}/{n_packets} '
                f'({len(all_raw)} mot(s)-clé(s) déjà accumulé(s))'
            )
            await _send_notification(
                rule_id,
                '[Sentinel] 🔄 Auto-apprentissage repris',
                f'**Fichier :** `{log_path}`\n'
                f'**Reprise :** paquet {resume_from + 1}/{n_packets} '
                f'({len(all_raw)} mot(s)-clé(s) déjà accumulé(s))'
            )
        else:
            await _send_notification(
                rule_id,
                '[Sentinel] 🚀 Auto-apprentissage démarré',
                f'**Fichier :** `{log_path}`\n'
                f'**Période :** {period_start.strftime("%Y-%m-%d %H:%M")} UTC → '
                f'{period_end.strftime("%Y-%m-%d %H:%M")} UTC\n'
                f'**Granularité :** {gran_label} — {n_packets} paquets prévus'
            )

        _db_update(session_id, status='scanning')

        # Vérification rapide : le fichier est-il lisible ?
        if not os.path.isfile(log_path):
            _db_update(session_id, status='error',
                       error_message=f"Fichier introuvable sur le serveur : {log_path}")
            return
        if not os.access(log_path, os.R_OK):
            _db_update(session_id, status='error',
                       error_message=f"Fichier non lisible (permissions insuffisantes) : {log_path}")
            return

        has_ts = _detect_timestamps(log_path)
        fallback_offset = 0

        if resume_from > 0 and not has_ts:
            # Approximate fallback_offset for non-timestamp logs
            fallback_offset = resume_from * max_chars


        from app.services.ollama_service import OllamaService
        db = SessionLocal()
        try:
            cfg = db.query(GlobalConfig).first()
            ollama_url   = cfg.ollama_url   if cfg else 'http://ollama:11434'
            ollama_model = cfg.ollama_model if cfg else 'gemma:2b'
            ollama_think = cfg.ollama_think if cfg else False
            ollama_temp  = cfg.ollama_temp  if cfg else 0.1
            ollama_ctx   = cfg.ollama_ctx   if cfg else 4096
        finally:
            db.close()

        ollama = OllamaService()

        for packet_idx in range(resume_from, n_packets):
            # Check for cancellation
            current_status = _db_session_get_status(session_id)
            if current_status in ('error', 'reverted'):
                return

            t_start = period_start + timedelta(seconds=packet_idx * granularity_s)
            t_end   = min(period_end, t_start + timedelta(seconds=granularity_s))
            window_label = (f'{t_start.strftime("%Y-%m-%d %H:%M")} → '
                            f'{t_end.strftime("%H:%M")} UTC')

            # ⏳ Wait for this packet's window to elapse if it's in the future
            now_utc = datetime.utcnow()
            if t_end > now_utc:
                wait_secs = (t_end - now_utc).total_seconds()
                app_logger.info(
                    'KeywordLearning',
                    f'Session {session_id} — paquet {packet_idx+1}/{n_packets}: '
                    f'attente de {wait_secs:.0f}s jusqu\'à {t_end.strftime("%H:%M")} UTC'
                )
                _db_update(session_id,
                           current_window=f'En attente… {window_label}')
                slept = 0.0
                while slept < wait_secs:
                    interval = min(30.0, wait_secs - slept)
                    await asyncio.sleep(interval)
                    slept += interval
                    # Cancellation check during wait
                    current_status = _db_session_get_status(session_id)
                    if current_status in ('error', 'reverted'):
                        return

            _db_update(session_id, current_window=window_label)

            text, fallback_offset = _read_window(
                log_path, t_start, t_end, max_chars, has_ts, fallback_offset)

            if not text.strip():
                _db_update(session_id, completed_packets=packet_idx + 1)
                continue

            prompt = _PROMPT_PHASE1.format(window=window_label, lines=text)

            try:
                response = await asyncio.wait_for(
                    ollama.analyze_async(
                        prompt=prompt,
                        url=ollama_url,
                        model=ollama_model,
                        think=ollama_think,
                        options={'temperature': ollama_temp, 'num_ctx': ollama_ctx}
                    ),
                    timeout=120.0
                )
            except asyncio.TimeoutError:
                response = '[]'

            # Log raw response for debugging
            app_logger.debug(
                'KeywordLearning',
                f'Session {session_id} paquet {packet_idx+1} — réponse brute ({len(response)} car.) : '
                f'{response[:300].replace(chr(10), " ")}{"…" if len(response) > 300 else ""}'
            )
            packet_kws = _extract_json_list(response)
            app_logger.debug(
                'KeywordLearning',
                f'Session {session_id} paquet {packet_idx+1} — {len(packet_kws)} mot(s)-clé(s) extraits : {packet_kws}'
            )
            _log_exchange(sess_log,
                          f"PHASE 1 — Packet {packet_idx+1}/{n_packets} ({window_label})",
                          prompt, response,
                          f"Extracted: {packet_kws}")

            # Accumulate unique keywords
            for kw in packet_kws:
                kw_clean = kw.strip().lower()
                if kw_clean and kw_clean not in [k.lower() for k in all_raw]:
                    all_raw.append(kw.strip())
            all_raw = all_raw[:40]  # cap phase-1 at 40

            log_entries.append({
                'packet_idx': packet_idx,
                'window': window_label,
                'chars': len(text),
                'keywords': packet_kws,
            })

            # First packet notification
            if not first_packet_sent and packet_kws:
                first_packet_sent = True
                await _send_notification(
                    rule_id,
                    '[Sentinel] 📦 Premier paquet analysé',
                    f'**Fenêtre :** {window_label}\n'
                    f'**Candidats initiaux :** {", ".join(packet_kws[:10])}'
                )

            _db_update(
                session_id,
                completed_packets=packet_idx + 1,
                raw_keywords_json=json.dumps(all_raw),
                ollama_log_json=json.dumps(log_entries),
            )

        # Notification: liste initiale
        await _send_notification(
            rule_id,
            '[Sentinel] 📋 Liste initiale de mots-clés',
            f'**{len(all_raw)} candidat(s)** extraits de {n_packets} paquets :\n'
            + (', '.join(f'`{k}`' for k in all_raw) if all_raw else '_aucun_')
        )

        # Guard: if no candidates at all, don't hallucinate — abort cleanly
        if not all_raw:
            _db_update(
                session_id,
                status='error',
                error_message=(
                    'Aucun mot-clé candidat trouvé sur l\'ensemble de la période. '
                    'Vérifiez que le fichier contient bien des logs sur cette période '
                    'et que les timestamps sont lisibles.'
                )
            )
            return

        # ── Phase 2: initial refine ───────────────────────────────────────────
        _db_update(session_id, status='refining')

        sample = _read_sample(log_path, max_chars)
        prompt2 = _PROMPT_PHASE2.format(
            n_raw=len(all_raw),
            n_packets=n_packets,
            raw_keywords=json.dumps(all_raw),
            sample=sample,
        )

        try:
            response2 = await asyncio.wait_for(
                ollama.analyze_async(
                    prompt=prompt2,
                    url=ollama_url,
                    model=ollama_model,
                    think=ollama_think,
                    options={'temperature': ollama_temp, 'num_ctx': ollama_ctx}
                ),
                timeout=180.0
            )
        except asyncio.TimeoutError:
            response2 = json.dumps({'keywords': all_raw[:15], 'rationale': {}})

        final_kws, rationale = _extract_json_phase2(response2)
        final_kws = final_kws[:15]
        _log_exchange(sess_log, "PHASE 2 — Initial refinement", prompt2, response2,
                      f"Initial refined list: {final_kws}")

        await _send_notification(
            rule_id,
            '[Sentinel] ✂️ Liste raffinée (validation en cours…)',
            f'**Candidats retenus :** {" | ".join(f"`{k}`" for k in final_kws)}\n'
            f'_Validation croisée Ollama en cours…_'
        )

        # ── Double-validation loop (max 3 attempts) ───────────────────────────
        MAX_VALIDATION = 3
        phrase_extraction_done = False

        for val_attempt in range(1, MAX_VALIDATION + 1):
            # STEP 2: cross-validate YES/NO
            await _send_notification(
                rule_id,
                f'[Sentinel] 🔍 Validation croisée ({val_attempt}/{MAX_VALIDATION})…',
                f'Ollama vérifie la pertinence de la liste raffinée par rapport aux candidats bruts.'
            )
            is_valid = await _validate_refined_list(
                ollama, all_raw, final_kws,
                ollama_url, ollama_model, ollama_think, ollama_temp, ollama_ctx,
                sess_log, val_attempt
            )

            if not is_valid:
                # STEP 4: retry with shuffle
                if val_attempt >= MAX_VALIDATION:
                    # 3 failures → cancel
                    cancel_reason = (
                        f'Ollama a rejeté la liste raffinée {MAX_VALIDATION} fois de suite. '
                        f'La liste brute contenait : {json.dumps(all_raw[:10])}. '
                        f'Consultez le fichier log de session pour les détails.'
                    )
                    _log_footer(sess_log, success=False, keywords=[], reason=cancel_reason)
                    _db_update(session_id, status='error', error_message=cancel_reason)
                    await _send_notification(
                        rule_id,
                        '[Sentinel] ❌ Auto-apprentissage annulé',
                        f'**Échec après {MAX_VALIDATION} tentatives de validation.**\n\n'
                        f'{cancel_reason}\n\n'
                        f'_Téléchargez le log de session depuis la page Règles pour analyser les échanges._'
                    )
                    return

                # Retry with shuffled prompt
                await _send_notification(
                    rule_id,
                    f'[Sentinel] 🔄 Reformulation ({val_attempt+1}/{MAX_VALIDATION})…',
                    f'La liste n\'était pas pertinente. Nouvel essai avec une présentation différente.'
                )
                try:
                    final_kws, rationale = await _refine_with_shuffle(
                        ollama, all_raw, sample,
                        ollama_url, ollama_model, ollama_think, ollama_temp, ollama_ctx,
                        sess_log, val_attempt
                    )
                    final_kws = final_kws[:15]
                except Exception as shuffle_err:
                    # Log in session file so user can see it
                    _log_exchange(sess_log,
                                  f"RETRY (shuffle #{val_attempt}) — ERREUR",
                                  "(shuffle)", str(shuffle_err),
                                  f"⚠️ Erreur lors du shuffle-retry : {shuffle_err}. Poursuite avec la liste précédente.")
                    app_logger.error('KeywordLearning',
                                     f'Session {session_id}: shuffle-retry #{val_attempt} failed: {shuffle_err}')
                    # Don't abort — continue loop with unchanged final_kws
                continue  # back to STEP 2


            # STEP 5: YES — check for phrases
            if not phrase_extraction_done:
                real_kws, phrases = _split_kws_and_phrases(final_kws)
                if phrases:
                    phrase_extraction_done = True
                    await _send_notification(
                        rule_id,
                        '[Sentinel] ✂️ Extraction mots-clefs depuis phrases…',
                        f'{len(phrases)} entrée(s) trop longue(s) détectée(s). '
                        f'Ollama extrait les mots-clefs importants.'
                    )
                    final_kws = await _extract_keywords_from_phrases(
                        ollama, phrases, real_kws,
                        ollama_url, ollama_model, ollama_think, ollama_temp, ollama_ctx,
                        sess_log
                    )
                    continue  # back to STEP 2 to re-validate the shortened list

            # STEP 6: list is valid and all keywords are short → done
            break

        # Save final refined list
        _db_update(
            session_id,
            final_keywords_json=json.dumps(final_kws),
            refine_rationale_json=json.dumps(rationale),
        )

        rationale_lines = '\n'.join(
            f'- **{k}** — {v}' for k, v in list(rationale.items())[:10]
        )
        await _send_notification(
            rule_id,
            '[Sentinel] ✂️ Liste raffinée (validée)',
            f'**Mots-clés retenus :** {" | ".join(f"`{k}`" for k in final_kws)}\n'
            + (f'\n{rationale_lines}' if rationale_lines else '')
        )

        # Write success footer
        _log_footer(sess_log, success=True, keywords=final_kws)

        # ── Auto-validate ─────────────────────────────────────────────────────
        await _do_validate(session_id, final_kws)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        app_logger.error('KeywordLearning', f'Session {session_id} error: {e}\n{tb}')
        _db_update(session_id, status='error', error_message=str(e))
        # Also write to session log file if we have it
        try:
            _log_exchange(sess_log,
                          "ERREUR FATALE",
                          "(interne)", str(e),
                          f"⛔ Exception inattendue — session annulée.\n{tb}")
        except Exception:
            pass



async def _do_validate(session_id: int, keywords: list[str]):
    """Apply keywords to the rule and mark session as validated."""
    rule_id = None
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if not s:
            return
        rule_id = s.rule_id
        if rule_id:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule:
                rule.set_keywords(keywords)
                db.commit()
        s.final_keywords_json = json.dumps(keywords)
        s.status = 'validated'
        s.validated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    await _send_notification(
        rule_id,
        '[Sentinel] ✅ Mots-clés validés automatiquement',
        f'**Mots-clés appliqués à la règle :**\n'
        f'{" | ".join(f"`{k}`" for k in keywords)}\n\n'
        f'_Vous pouvez réviser ou annuler ce résultat depuis la page Règles._'
    )


# ── Public API helpers ─────────────────────────────────────────────────────────

async def revaluate_session(session_id: int, current_keywords: list[str]):
    """Re-run phase 2 with a modified keyword list (user-edited)."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if not s:
            return
        log_path  = _resolve_log_path(s.log_file_path)
        max_chars = s.max_chars_per_packet
        n_packets = s.total_packets
        rule_id   = s.rule_id
        s.status  = 'refining'
        s.raw_keywords_json = json.dumps(current_keywords)
        db.commit()
    finally:
        db.close()

    from app.services.ollama_service import OllamaService
    db = SessionLocal()
    try:
        cfg = db.query(GlobalConfig).first()
        ollama_url   = cfg.ollama_url   if cfg else 'http://ollama:11434'
        ollama_model = cfg.ollama_model if cfg else 'gemma:2b'
        ollama_think = cfg.ollama_think if cfg else False
        ollama_temp  = cfg.ollama_temp  if cfg else 0.1
        ollama_ctx   = cfg.ollama_ctx   if cfg else 4096
    finally:
        db.close()

    sample = _read_sample(log_path, max_chars)
    prompt = _PROMPT_PHASE2.format(
        n_raw=len(current_keywords),
        n_packets=n_packets,
        raw_keywords=json.dumps(current_keywords),
        sample=sample,
    )

    ollama = OllamaService()
    try:
        response = await asyncio.wait_for(
            ollama.analyze_async(
                prompt=prompt, url=ollama_url, model=ollama_model,
                think=ollama_think,
                options={'temperature': ollama_temp, 'num_ctx': ollama_ctx}
            ),
            timeout=180.0
        )
    except asyncio.TimeoutError:
        response = json.dumps({'keywords': current_keywords[:15], 'rationale': {}})

    final_kws, rationale = _extract_json_phase2(response)
    final_kws = final_kws[:15]
    _db_update(
        session_id,
        final_keywords_json=json.dumps(final_kws),
        refine_rationale_json=json.dumps(rationale),
        status='refining_done',  # will be picked up by polling
    )
    await _do_validate(session_id, final_kws)


async def validate_session(session_id: int, keywords: list[str]):
    """Manual validation with a custom keyword list."""
    await _do_validate(session_id, keywords)


async def revert_session(session_id: int) -> list[str]:
    """Restore the keywords that existed before the learning session."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if not s:
            return []
        prev = json.loads(s.previous_keywords_json or '[]')
        rule_id = s.rule_id
        if rule_id:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule:
                rule.set_keywords(prev)
                db.commit()
        s.status = 'reverted'
        db.commit()
        return prev
    finally:
        db.close()


def get_session_status(session_id: int) -> Optional[dict]:
    """Return a polling-friendly dict of the session state."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(
            KeywordLearningSession.id == session_id).first()
        if not s:
            return None

        log_entries = json.loads(s.ollama_log_json or '[]')
        # Build current packet preview (last packet's lines, first 5)
        current_preview = []
        if log_entries:
            last = log_entries[-1]
            current_preview = last.get('keywords', [])

        return {
            'id': s.id,
            'status': s.status,
            'total_packets': s.total_packets,
            'completed_packets': s.completed_packets,
            'current_window': (log_entries[-1]['window'] if log_entries else None),
            'current_packet_keywords': current_preview,
            'raw_keywords': json.loads(s.raw_keywords_json or '[]'),
            'final_keywords': json.loads(s.final_keywords_json or '[]'),
            'refine_rationale': json.loads(s.refine_rationale_json or '{}'),
            'previous_keywords': json.loads(s.previous_keywords_json or '[]'),
            'error_message': s.error_message,
            'validated_at': s.validated_at.isoformat() + 'Z' if s.validated_at else None,
            'log_file_path': s.log_file_path,
            'granularity_s': s.granularity_s,
            'period_start': s.period_start.isoformat() + 'Z' if s.period_start else None,
            'period_end':   s.period_end.isoformat()   + 'Z' if s.period_end   else None,
        }
    finally:
        db.close()
