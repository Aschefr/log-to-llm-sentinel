import asyncio
import html
import json
import os
import re
import collections
import statistics
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, ResolutionVerdict
from app.services import monitoring
from app.utils.notification_i18n import nt
from app.services.alert_analysis import analysis_lines, short_summary, messages_to_text, _format_duration
from app.services.app_config import config_to_dict, llm_options
from app.services.log_source import resolve_path, read_tail_lines
from app.utils import keyword_match
from app.services.prompts import RESOLUTION_SCHEMA, RESOLUTION_SYSTEM, resolution_label
from app.services.probe_service import run_probe, PROBE_TYPES
from app import logger


# ── Seuils de confiance IA ────────────────────────────────────────────────
AI_CONFIDENCE_THRESHOLD = 50       # Seuil standard
AI_CONFIDENCE_THRESHOLD_LOW = 30   # Seuil reduit si pattern bien valide (weight >= 3)
AI_CONFIDENCE_WEIGHT_CUTOFF = 3    # Nombre de validations minimum pour seuil reduit

SEVERITY_LEVELS = {"info": 0, "warning": 1, "critical": 2}

# ── Decay temporel des patterns ──────────────────────────────────────────
PATTERN_DECAY_DAYS = 14            # Jours sans validation avant decrement automatique
PATTERN_DECAY_INTERVAL_HOURS = 6   # Intervalle de verification du decay

# ── Garde-fous des tentatives ────────────────────────────────────────────
RETRY_COOLDOWN_MINUTES = 5         # Attente apres un rejet avant une nouvelle tentative par motif
                                   # (apres un rejet par timeout : un delai complet)
VERDICT_RETENTION_DAYS = 30        # Les verdicts plus anciens sont purges...
VERDICT_KEEP_PER_RULE = 20         # ...sauf les N plus recents de chaque regle

# ── Calme requis et sonde ────────────────────────────────────────────────
ADAPTIVE_CALM_FACTOR = 3           # Calme adaptatif : 3 x l'intervalle median entre les erreurs de l'incident...
ADAPTIVE_CALM_MAX_MINUTES = 1440   # ...plafonne a 24 h (et jamais sous le timeout de la regle)
MAX_ERROR_TIMES = 20               # Horodatages d'erreur conserves par incident
PROBE_REFRESH_SECONDS = 60         # Frequence de la sonde pendant un incident
MAX_PENDING_SUGGESTIONS = 10       # Suggestions de motifs en attente de validation, par regle

# ── Auto-apprentissage : motifs refuses ──────────────────────────────────
LEARNED_PATTERN_MIN_LEN = 5
GENERIC_PATTERNS = {
    "success", "successful", "successfully", "succeeded", "finished", "started", "starting",
    "running", "stopped", "exiting", "queued", "restart", "restarted", "normal", "level=info",
    "message repeated", "repeated",
}
# Un motif de retour à la normale ne décrit pas un échec
FAILURE_WORDS = re.compile(
    r"\b(fail(ed|ure|s|ing)?|errors?|denied|refused|time[ds]?[ -]?out|unable|cannot|can't|could not|exception|fatal|"
    r"critical|warn(ing)?|unreachable|invalid|unauthori[sz]ed|forbidden|lost|panic|crash(ed)?|abort(ed)?|killed|"
    r"exceed(ed|s)?|exit(ing|ed)|terminat(ed|ing)|unhealthy|degraded|segfault|oom|out of memory|no such|not found|"
    r"échec|erreur|refusé|impossible)\b", re.IGNORECASE)


def clean_ollama_json(response: str) -> str:
    """Nettoie une réponse Ollama en retirant les blocs markdown ```json ... ```.
    Utilitaire réutilisable pour tout parsing de JSON provenant d'un LLM."""
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    return cleaned


def _get_notification_config(config: GlobalConfig) -> dict:
    """Construit le dictionnaire de configuration de notification à partir de GlobalConfig.
    Évite la duplication dans chaque méthode d'envoi."""
    return {
        "smtp_host": config.smtp_host,
        "smtp_port": config.smtp_port,
        "smtp_user": config.smtp_user,
        "smtp_password": config.smtp_password,
        "smtp_recipient": config.smtp_recipient,
        "smtp_tls": config.smtp_tls,
        "smtp_ssl_mode": config.smtp_ssl_mode,
        "notification_method": config.notification_method,
        "apprise_url": config.apprise_url,
        "apprise_tags": config.apprise_tags,
        "apprise_max_chars": config.apprise_max_chars,
        "discord_webhook_url": config.discord_webhook_url,
    }


def _read_tail_lines(file_path: str, n: int = 30) -> List[str]:
    """Lit les N dernières lignes d'un fichier sans charger tout le fichier en mémoire.
    Utilise collections.deque pour un tail efficace en O(n)."""
    try:
        if not os.path.exists(file_path):
            return []
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            tail = list(collections.deque(f, n))
        return [l.strip() for l in tail]
    except Exception as ex:
        logger.warning("ResolutionService", f"Impossible de lire les dernières lignes de {file_path}: {ex}")
        return []


def _tail(log_file_path: str, n: int = 30) -> List[str]:
    """Dernières lignes non vides de la source d'une règle (fichier, webhook ou syslog)."""
    lines = read_tail_lines(resolve_path(log_file_path), max_bytes=256 * 1024)
    return [l.strip() for l in lines if l.strip()][-n:]


def _is_error_line(line: str, keywords: List[str], excluded: List[str]) -> bool:
    """Même critère que l'orchestrateur : la ligne déclencherait la règle."""
    return keyword_match.classify(line, keywords, excluded)[0] == "matched"


def _is_learnable_pattern(pattern: str, keywords: List[str], error_lines: List[str], context_lines: List[str]) -> bool:
    """Un motif appris doit être spécifique, absent des lignes d'erreur et réellement présent dans les logs."""
    low = pattern.lower()
    if len(low) < LEARNED_PATTERN_MIN_LEN or low in GENERIC_PATTERNS or FAILURE_WORDS.search(low):
        return False
    if any(k.lower() in low or low in k.lower() for k in keywords):
        return False
    if any(low in l.lower() for l in error_lines):
        return False
    return any(low in l.lower() for l in context_lines)


class ResolutionService:
    """Détecte et valide le retour à la normale des règles en alerte."""

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        # Structure de _alert_states:
        # rule_id -> {
        #     "status": str ("normal", "alert", "resolving"),
        #     "started_at": datetime,
        #     "last_error_at": datetime,
        #     "max_severity": str ("info", "warning", "critical"),
        #     "attempting": bool (une tentative de resolution est en cours),
        #     "next_attempt_at": datetime | None (pas de nouvelle tentative avant, apres un rejet),
        #     "lines_since_error": int | None (lignes normales recues depuis la derniere erreur ; None = inconnu),
        # }
        self._alert_states = {}
        self._background = set()  # Validations de résolution en cours, gardées vivantes jusqu'à leur fin
        self._state_lock = asyncio.Lock()

    def set_orchestrator(self, orchestrator):
        self.orchestrator = orchestrator

    def restore_states_from_db(self):
        """Restaure les etats d'alerte depuis la base de donnees au demarrage."""
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.alert_status != "normal").all()
            for rule in rules:
                max_sev = self._get_max_severity_for_rule(rule.id, db)
                # Une validation interrompue par l'arret ne reprendra pas : la regle repasse en alerte
                if rule.alert_status == "resolving":
                    rule.alert_status = "alert"
                error_times = [a.analyzed_at for a in db.query(Analysis).filter(
                    Analysis.rule_id == rule.id, Analysis.resolution_status == None, Analysis.analyzed_at != None
                ).order_by(Analysis.analyzed_at).all()][-MAX_ERROR_TIMES:]
                last_error_at = (error_times[-1] if error_times else None) or rule.alert_started_at or datetime.utcnow()
                self._alert_states[rule.id] = {
                    "status": rule.alert_status,
                    "started_at": rule.alert_started_at or last_error_at,
                    "last_error_at": last_error_at,
                    "max_severity": max_sev,
                    "lines_since_error": None,
                    "error_times": error_times,
                }
                logger.info("ResolutionService", f"Etat d'alerte restaure pour la regle '{rule.name}': {rule.alert_status} (severite max: {max_sev})")
            db.commit()
        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de la restauration des etats d'alerte : {e}")
        finally:
            db.close()

    def _get_max_severity_for_rule(self, rule_id: int, db) -> str:
        """Retourne la severite max parmi les analyses non resolues d'une regle."""
        try:
            analyses = db.query(Analysis).filter(
                Analysis.rule_id == rule_id,
                Analysis.resolution_status == None
            ).all()
            if not analyses:
                return "info"
            max_val = 0
            max_sev = "info"
            for a in analyses:
                val = SEVERITY_LEVELS.get(a.severity or "info", 0)
                if val > max_val:
                    max_val = val
                    max_sev = a.severity
            return max_sev
        except Exception:
            return "info"

    def _should_notify_for_rule(self, db, rule: Rule) -> bool:
        """Verifie si la severite max de l'incident depasse le seuil de notification de la regle.
        Option 1 validee : filtrage total de toutes les notifications de resolution (search + denied + resolved)."""
        threshold = getattr(rule, "notify_severity_threshold", "info") or "info"
        if threshold == "info":
            return True  # Seuil info = toutes les notifications passent
        max_sev = self._alert_states.get(rule.id, {}).get("max_severity", "info")
        sev_val = SEVERITY_LEVELS.get(max_sev, 0)
        threshold_val = SEVERITY_LEVELS.get(threshold, 0)
        if sev_val < threshold_val:
            logger.debug("ResolutionService", f"Notification de resolution ignoree pour '{rule.name}' : severite max '{max_sev}' < seuil '{threshold}'")
            return False
        return True

    def _record_verdict(self, db, rule_id: int, trigger: str, outcome: str,
                        ai_resolved: bool = None, ai_confidence: int = None,
                        ai_explanation: str = None, max_severity: str = None,
                        context_lines: list = None, resolution_line: str = None,
                        resolution_patterns: list = None):
        """Enregistre un verdict de resolution dans la table resolution_verdicts."""
        try:
            verdict = ResolutionVerdict(
                rule_id=rule_id,
                trigger=trigger,
                ai_resolved=ai_resolved,
                ai_confidence=ai_confidence,
                ai_explanation=ai_explanation,
                outcome=outcome,
                max_severity=max_severity,
                context_lines_json=json.dumps((context_lines or [])[-30:]),  # Max 30 lignes pour audit
                resolution_line=resolution_line,
                resolution_patterns_json=json.dumps(resolution_patterns or []),
            )
            db.add(verdict)
            db.commit()
            db.refresh(verdict)
            logger.debug("ResolutionService", f"Verdict enregistre : rule_id={rule_id}, outcome='{outcome}', confidence={ai_confidence}")
            return verdict
        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de l'enregistrement du verdict : {e}")
            return None

    async def on_error_detected(self, rule_id: int, severity: str = "info"):
        """Appele par l'Orchestrateur quand un match d'erreur est detecte.
        Passe la regle en etat 'alert' si elle ne l'est pas deja. Met a jour max_severity."""
        async with self._state_lock:
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    return

                # Garde explicite : si la resolution est desactivee, ne pas gerer les etats
                if rule.resolution_mode == "disabled":
                    return

                now = datetime.utcnow()
                if rule_id not in self._alert_states or self._alert_states[rule_id]["status"] == "normal":
                    self._alert_states[rule_id] = {
                        "status": "alert",
                        "started_at": now,
                        "last_error_at": now,
                        "max_severity": severity,
                        "lines_since_error": 0,
                        "error_times": [now],
                    }
                    rule.alert_status = "alert"
                    rule.alert_started_at = now
                    db.commit()
                    logger.info("ResolutionService", f"La regle '{rule.name}' passe en etat ALERT (severite: {severity})")
                else:
                    self._alert_states[rule_id]["status"] = "alert"
                    self._alert_states[rule_id]["last_error_at"] = now
                    self._alert_states[rule_id]["lines_since_error"] = 0
                    self._alert_states[rule_id]["next_attempt_at"] = None
                    times = self._alert_states[rule_id].setdefault("error_times", [])
                    times.append(now)
                    del times[:-MAX_ERROR_TIMES]
                    # Mise a jour max_severity si la nouvelle est plus haute
                    current_max = self._alert_states[rule_id].get("max_severity", "info")
                    if SEVERITY_LEVELS.get(severity, 0) > SEVERITY_LEVELS.get(current_max, 0):
                        self._alert_states[rule_id]["max_severity"] = severity
                    if rule.alert_status != "alert":
                        rule.alert_status = "alert"
                        db.commit()
                    logger.debug("ResolutionService", f"Erreur repetee pour la regle '{rule.name}', maj last_error_at (severite: {severity})")
            except Exception as e:
                logger.error("ResolutionService", f"Erreur dans on_error_detected : {e}")
            finally:
                db.close()

    async def on_new_lines(self, rule: Rule, lines: List[str]):
        """Appele pour chaque lot de nouvelles lignes (toutes, pas seulement les matchs).
        Verifie si un pattern de resolution est detecte. Trie les patterns par poids decroissant."""
        # Garde explicite : si la resolution est desactivee, on ne verifie rien
        if rule.resolution_mode == "disabled":
            return

        state = self._alert_states.get(rule.id)
        if not state or state["status"] not in ("alert", "resolving"):
            return

        # Un motif de resolution ne peut pas venir d'une ligne qui declenche elle-meme la regle
        keywords = rule.get_keywords()
        excluded = rule.get_excluded_patterns()
        lines = [l for l in lines if not _is_error_line(l, keywords, excluded)]
        if state.get("lines_since_error") is not None:
            state["lines_since_error"] += len(lines)

        if rule.resolution_mode not in ("pattern", "both"):
            return

        # Une seule tentative a la fois, et pas de nouvel essai juste apres un rejet
        next_attempt_at = state.get("next_attempt_at")
        if state.get("attempting") or (next_attempt_at and datetime.utcnow() < next_attempt_at):
            return

        # Tri des patterns par poids decroissant (patterns les plus fiables en priorite)
        weighted = rule.get_active_weighted_patterns()
        weighted_sorted = sorted(weighted, key=lambda x: x.get("weight", 1), reverse=True)
        patterns = [(item["pattern"], item.get("weight", 1)) for item in weighted_sorted if item.get("pattern")]

        if not patterns:
            return

        matched_line = None
        matched_pattern = None
        matched_weight = 1
        for line in lines:
            if len(line) > 10_000:
                continue
            for pat, weight in patterns:
                if pat.lower() in line.lower():
                    matched_line = line
                    matched_pattern = pat
                    matched_weight = weight
                    break
            if matched_line:
                break

        if matched_line:
            logger.info("ResolutionService", f"Pattern de resolution '{matched_pattern}' (poids={matched_weight}) detecte dans : {matched_line[:120]}")
            # En arrière-plan : la validation IA peut durer des minutes, la réception des lignes ne doit pas l'attendre
            task = asyncio.create_task(self._try_resolve(rule.id, trigger=f"Pattern match: '{matched_pattern}'", context_lines=lines, resolution_line=matched_line,
                                    resolution_patterns=[matched_pattern], use_ai=(rule.resolution_ai_mode or "ambiguous") != "never"))
            self._background.add(task)
            task.add_done_callback(self._background.discard)


    async def check_timeout_resolutions(self):
        """Boucle périodique — vérifie si le timeout d'absence d'erreur est atteint."""
        db = SessionLocal()
        try:
            if monitoring.is_paused(db):
                return
            for rule_id, state in list(self._alert_states.items()):
                # Ignorer 'resolving' et les tentatives en cours : une validation IA est déjà lancée
                if state["status"] != "alert" or state.get("attempting"):
                    continue

                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule or not rule.enabled:
                    continue

                # Garde explicite
                if rule.resolution_mode == "disabled":
                    continue

                now = datetime.utcnow()
                has_probe = rule.probe_type in PROBE_TYPES and bool((rule.probe_target or "").strip())
                if has_probe:
                    probe = state.get("probe")
                    if not probe or (now - probe["at"]).total_seconds() >= PROBE_REFRESH_SECONDS:
                        state["probe"] = await run_probe(rule.probe_type, rule.probe_target, rule.probe_expect)

                calm_mins = self.required_calm_minutes(rule, state)
                last_err = state["last_error_at"]
                next_attempt_at = state.get("next_attempt_at")

                if last_err and (now - last_err >= timedelta(minutes=calm_mins)) and (not next_attempt_at or now >= next_attempt_at):
                    # Tant que la sonde échoue, l'incident reste ouvert, même si le log est muet
                    if has_probe and not state["probe"]["ok"]:
                        continue
                    logger.info("ResolutionService", f"Calme requis atteint ({calm_mins} min) pour la règle '{rule.name}'")
                    use_ai = (rule.resolution_ai_mode or "ambiguous") == "always"
                    trigger = f"Timeout ({calm_mins} min)" + (" + probe OK" if has_probe else "")
                    # Les dernières lignes du log servent de preuve à la validation IA (pas d'auto-apprentissage sur timeout)
                    context = await asyncio.to_thread(_tail, rule.log_file_path) if use_ai and rule.log_file_path else []
                    await self._try_resolve(rule_id, trigger=trigger, context_lines=context,
                                            resolution_line=f"Inactivité de l'erreur supérieure à {calm_mins} minutes",
                                            resolution_patterns=[], use_ai=use_ai)
        except Exception as e:
            logger.error("ResolutionService", f"Erreur dans check_timeout_resolutions : {e}")
        finally:
            db.close()

    def shift_timers(self, paused_for: timedelta):
        """Fin d'une pause de la surveillance : les délais de calme reprennent là où ils en étaient."""
        for state in self._alert_states.values():
            for key in ("last_error_at", "next_attempt_at"):
                if state.get(key):
                    state[key] += paused_for

    def required_calm_minutes(self, rule: Rule, state: dict) -> int:
        """Durée sans erreur exigée avant de clore l'incident.
        Adaptatif : ADAPTIVE_CALM_FACTOR x l'intervalle médian entre les erreurs de l'incident, au moins le timeout de la règle."""
        base = rule.resolution_timeout_minutes or 30
        if (rule.resolution_calm_mode or "adaptive") != "adaptive":
            return base
        times = sorted(state.get("error_times") or [])
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:]) if b > a]
        if not gaps:
            return base
        return int(min(max(base, ADAPTIVE_CALM_FACTOR * statistics.median(gaps)), ADAPTIVE_CALM_MAX_MINUTES))

    def incident_snapshot(self, rule: Rule) -> Optional[dict]:
        """État de l'incident en cours pour l'interface (None si la règle est normale)."""
        state = self._alert_states.get(rule.id)
        if not state or state.get("status", "normal") == "normal":
            return None
        now = datetime.utcnow()
        calm = self.required_calm_minutes(rule, state)
        last_err = state.get("last_error_at")
        next_check = last_err + timedelta(minutes=calm) if last_err else None
        if state.get("next_attempt_at") and (not next_check or state["next_attempt_at"] > next_check):
            next_check = state["next_attempt_at"]
        probe = state.get("probe")
        return {
            "status": state.get("status"),
            "started_at": state.get("started_at"),
            "last_error_at": last_err,
            "max_severity": state.get("max_severity", "info"),
            "required_calm_minutes": calm,
            "calm_elapsed_minutes": int((now - last_err).total_seconds() // 60) if last_err else None,
            "next_check_at": next_check,
            "lines_since_error": state.get("lines_since_error"),
            "attempting": bool(state.get("attempting")),
            "probe": dict(probe) if probe else None,
        }

    def _defer_next_attempt(self, rule: Rule, pattern_triggered: bool):
        """Apres un rejet : pas de nouvelle tentative avant un delai (un calme complet pour le declencheur timeout)."""
        state = self._alert_states.get(rule.id) or {}
        minutes = RETRY_COOLDOWN_MINUTES if pattern_triggered else self.required_calm_minutes(rule, state)
        if rule.id in self._alert_states:
            self._alert_states[rule.id]["next_attempt_at"] = datetime.utcnow() + timedelta(minutes=minutes)

    async def _try_resolve(self, rule_id: int, trigger: str, context_lines: List[str], resolution_line: str = None,
                           resolution_patterns: List[str] = None, use_ai: bool = False):
        """Tente la resolution avec validation IA optionnelle.
        Enregistre systematiquement un ResolutionVerdict pour chaque tentative.

        Pipeline decoupage en 3 phases pour minimiser la duree de detention du verrou :
          Phase 1 (lock court) : lecture config + transition vers 'resolving'
          Phase 2 (sans lock)  : appel IA potentiellement long (jusqu'a 180s)
          Phase 3 (lock court) : application du resultat en memoire et BDD
        Une seule tentative par regle a la fois ; elle est abandonnee si une erreur survient pendant la validation.
        """
        # === Phase 1 : Transition vers 'resolving' ===
        max_severity = "info"
        pattern_triggered = bool(resolution_patterns)

        async with self._state_lock:
            state = self._alert_states.get(rule_id)
            if not state or state.get("attempting"):
                return
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    return

                max_severity = state.get("max_severity", "info")
                error_snapshot = state.get("last_error_at")

                state["status"] = "resolving"
                rule.alert_status = "resolving"
                db.commit()
                state["attempting"] = True
            except Exception as e:
                logger.error("ResolutionService", f"Erreur phase 1 de _try_resolve pour rule_id {rule_id} : {e}")
                return
            finally:
                db.close()

        try:
            # === Phase 2 : Appel IA (sans lock — potentiellement long) ===
            verdict = None
            ai_accepted = False
            low_confidence_rejected = False
            effective_threshold = AI_CONFIDENCE_THRESHOLD

            if use_ai:
                db = SessionLocal()
                try:
                    rule = db.query(Rule).filter(Rule.id == rule_id).first()
                    if not rule:
                        raise Exception("Regle introuvable avant appel IA")

                    verdict = await self._validate_with_ai(db, rule, trigger, context_lines, resolution_patterns, resolution_line)
                    confidence = verdict.get("confidence", 0)

                    # Seuil de confiance dynamique : si le pattern a un poids >= cutoff, seuil reduit
                    if resolution_patterns:
                        weighted = rule.get_weighted_resolution_patterns()
                        for item in weighted:
                            if item.get("pattern", "") in resolution_patterns:
                                if item.get("weight", 1) >= AI_CONFIDENCE_WEIGHT_CUTOFF:
                                    effective_threshold = AI_CONFIDENCE_THRESHOLD_LOW
                                    logger.debug("ResolutionService", f"Seuil de confiance reduit a {effective_threshold}% (pattern poids={item.get('weight')})")
                                break

                    if verdict.get("resolved") and confidence < effective_threshold:
                        logger.info("ResolutionService",
                                    f"Resolution acceptee par l'IA mais confiance trop basse ({confidence}% < {effective_threshold}%) pour '{rule.name}'. Rejet.")
                        low_confidence_rejected = True
                        verdict["resolved"] = False
                        verdict["explanation"] = f"{verdict.get('explanation', '')} [Rejete : confiance {confidence}% < seuil {effective_threshold}%]"

                    ai_accepted = bool(verdict.get("resolved"))

                except Exception as e:
                    logger.error("ResolutionService", f"Erreur phase 2 (appel IA) de _try_resolve pour rule_id {rule_id} : {e}")
                    # Restaurer l'etat 'alert' en memoire ET en BDD
                    async with self._state_lock:
                        db_err = SessionLocal()
                        try:
                            rule_err = db_err.query(Rule).filter(Rule.id == rule_id).first()
                            if rule_id in self._alert_states:
                                self._alert_states[rule_id]["status"] = "alert"
                            if rule_err:
                                rule_err.alert_status = "alert"
                                db_err.commit()
                                self._defer_next_attempt(rule_err, pattern_triggered)
                        except Exception:
                            pass
                        finally:
                            db_err.close()
                    return
                finally:
                    db.close()
            else:
                # Pas d'IA : resolution directe
                ai_accepted = True

            # === Phase 3 : Application du resultat (lock court) ===
            async with self._state_lock:
                db = SessionLocal()
                try:
                    rule = db.query(Rule).filter(Rule.id == rule_id).first()
                    if not rule:
                        return

                    # Une erreur est arrivee pendant la validation : le probleme n'est pas termine
                    state = self._alert_states.get(rule_id) or {}
                    if state.get("last_error_at") != error_snapshot:
                        logger.info("ResolutionService", f"Nouvelle erreur pendant la validation pour '{rule.name}' : tentative abandonnee.")
                        state["status"] = "alert"
                        rule.alert_status = "alert"
                        db.commit()
                        return

                    confidence = verdict.get("confidence", 0) if verdict else 0

                    if not use_ai:
                        # Resolution directe sans validation IA
                        self._record_verdict(db, rule_id, trigger, outcome="accepted_no_ai",
                                             max_severity=max_severity, context_lines=context_lines,
                                             resolution_line=resolution_line, resolution_patterns=resolution_patterns)
                        await self._mark_resolved(db, rule_id, trigger, context_lines=context_lines,
                                                  resolution_line=resolution_line, resolution_patterns=resolution_patterns)

                    elif low_confidence_rejected:
                        # Confiance IA insuffisante
                        self._record_verdict(db, rule_id, trigger, outcome="rejected_low_confidence",
                                             ai_resolved=True, ai_confidence=confidence,
                                             ai_explanation=verdict.get("explanation"),
                                             max_severity=max_severity, context_lines=context_lines,
                                             resolution_line=resolution_line, resolution_patterns=resolution_patterns)
                        self._alert_states[rule_id]["status"] = "alert"
                        rule.alert_status = "alert"
                        db.commit()
                        self._defer_next_attempt(rule, pattern_triggered)

                    elif ai_accepted:
                        # Resolution validee par l'IA : le motif declencheur gagne en fiabilite
                        logger.info("ResolutionService", f"Resolution validee par l'IA pour '{rule.name}' avec {confidence}% de confiance.")
                        for pat in resolution_patterns or []:
                            rule.increment_pattern_weight(pat)
                        self._record_verdict(db, rule_id, trigger, outcome="accepted",
                                             ai_resolved=True, ai_confidence=confidence,
                                             ai_explanation=verdict.get("explanation"),
                                             max_severity=max_severity, context_lines=context_lines,
                                             resolution_line=resolution_line, resolution_patterns=resolution_patterns)
                        await self._mark_resolved(db, rule_id, trigger,
                                                  ai_explanation=verdict.get("explanation"),
                                                  confidence=confidence,
                                                  context_lines=context_lines,
                                                  resolution_line=resolution_line,
                                                  resolution_patterns=resolution_patterns,
                                                  learn=pattern_triggered)

                    else:
                        # Rejetee par l'IA
                        self._record_verdict(db, rule_id, trigger, outcome="rejected_ai",
                                             ai_resolved=False, ai_confidence=confidence,
                                             ai_explanation=verdict.get("explanation") if verdict else None,
                                             max_severity=max_severity, context_lines=context_lines,
                                             resolution_line=resolution_line, resolution_patterns=resolution_patterns)
                        logger.info("ResolutionService", f"Resolution rejetee par l'IA pour '{rule.name}'. Explication: {verdict.get('explanation') if verdict else 'N/A'}")
                        self._alert_states[rule_id]["status"] = "alert"
                        rule.alert_status = "alert"
                        db.commit()
                        self._defer_next_attempt(rule, pattern_triggered)

                except Exception as e:
                    logger.error("ResolutionService", f"Erreur phase 3 de _try_resolve pour rule_id {rule_id} : {e}")
                    # Restaurer l'etat 'alert' en memoire ET en BDD (BUG-RES-02)
                    if rule_id in self._alert_states:
                        self._alert_states[rule_id]["status"] = "alert"
                    try:
                        rule.alert_status = "alert"
                        db.commit()
                    except Exception:
                        pass
                finally:
                    db.close()
        finally:
            if rule_id in self._alert_states:
                self._alert_states[rule_id]["attempting"] = False

    def _build_resolution_messages(self, db, rule: Rule, config: dict, context_lines: List[str],
                                   resolution_patterns: List[str] = None, resolution_line: str = None) -> list:
        """Messages de validation : incident (resumes des analyses non resolues), declencheur, vitalite du log, dernieres lignes."""
        lang = config.get("ollama_prompt_lang", "fr")
        state = self._alert_states.get(rule.id, {})
        now = datetime.utcnow()
        started = state.get("started_at") or now
        last_err = state.get("last_error_at") or started

        parts = [resolution_label(lang, "rule", name=rule.name)]
        if rule.application_context:
            parts.append(resolution_label(lang, "app_context", context=rule.application_context))
        parts.append("\n" + resolution_label(lang, "incident", started=f"{started:%Y-%m-%d %H:%M} UTC",
                                             duration=_format_duration((now - started).total_seconds()),
                                             since=int((now - last_err).total_seconds() // 60)))
        analyses = db.query(Analysis).filter(
            Analysis.rule_id == rule.id, Analysis.resolution_status == None
        ).order_by(Analysis.analyzed_at.desc()).limit(5).all()
        if analyses:
            parts.append(resolution_label(lang, "analyses"))
            for a in reversed(analyses):
                when = f"{a.analyzed_at:%Y-%m-%d %H:%M}" if a.analyzed_at else "?"
                parts.append(f"- [{when} UTC] {(a.severity or 'info').upper()} : {short_summary(a)}")

        if resolution_patterns:
            parts.append("\n" + resolution_label(lang, "check_pattern", pattern=resolution_patterns[0], line=(resolution_line or "")[:500]))
        else:
            parts.append("\n" + resolution_label(lang, "check_timeout", minutes=self.required_calm_minutes(rule, state)))
        count = state.get("lines_since_error")
        parts.append(resolution_label(lang, "liveness", count=count) if count is not None else resolution_label(lang, "liveness_unknown"))
        if state.get("probe"):
            parts.append(resolution_label(lang, "probe", detail=state["probe"]["detail"],
                                          result=resolution_label(lang, "probe_ok" if state["probe"]["ok"] else "probe_ko")))

        parts.append("\n" + resolution_label(lang, "recent"))
        parts.append("\n".join(l[:500] for l in context_lines[-30:]) if context_lines else resolution_label(lang, "no_recent"))

        system = RESOLUTION_SYSTEM.get(lang, RESOLUTION_SYSTEM["fr"])
        return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]

    async def _validate_with_ai(self, db, rule: Rule, trigger: str, context_lines: List[str],
                                resolution_patterns: List[str] = None, resolution_line: str = None) -> dict:
        """Demande a Ollama un verdict structure de retour a la normale."""
        try:
            config = config_to_dict(db.query(GlobalConfig).first())
            if not self.orchestrator:
                return {"resolved": False, "confidence": 0, "explanation": "Orchestrateur non injecté"}

            messages = self._build_resolution_messages(db, rule, config, context_lines, resolution_patterns, resolution_line)
            logger.debug("ResolutionService", f"Appel Ollama pour validation de résolution de '{rule.name}' ({trigger})...")

            async with self.orchestrator._ollama_semaphore:
                try:
                    res = await asyncio.wait_for(
                        self.orchestrator.ollama.chat(
                            messages,
                            url=config.get("ollama_url"),
                            model=config.get("ollama_model"),
                            think=config.get("ollama_think", False),
                            options=llm_options(config, temperature=0.1),
                            format=RESOLUTION_SCHEMA,
                        ),
                        timeout=180.0
                    )
                except asyncio.TimeoutError:
                    res = {"content": "", "error": "Timeout Ollama (180s)"}

            content = res.get("content") or ""
            logger.add_ollama_log(messages_to_text(messages), content or res.get("error") or "", "RESOLUTION")
            if res.get("error") or not content:
                return {"resolved": False, "confidence": 0, "explanation": f"Erreur Ollama : {res.get('error') or 'aucune réponse'}"}

            try:
                data = json.loads(clean_ollama_json(content))
                return {
                    "resolved": bool(data.get("resolved", False)),
                    "confidence": int(data.get("confidence", 0)),
                    "explanation": str(data.get("explanation", "Aucune explication fournie"))
                }
            except Exception as e:
                logger.error("ResolutionService", f"Erreur de parsing JSON de la réponse Ollama: {e}. Contenu: {content[:300]}")
                return {"resolved": False, "confidence": 0, "explanation": f"Erreur parsing réponse : {content[:100]}"}

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de la validation IA : {e}")
            return {"resolved": False, "confidence": 0, "explanation": str(e)}

    async def _mark_resolved(self, db, rule_id: int, trigger: str, ai_explanation: str = None, confidence: int = None, context_lines: List[str] = None, resolution_line: str = None, resolution_patterns: List[str] = None, learn: bool = True):
        """Passe la règle en 'normal', met à jour la BDD, et envoie la notification.
        Réutilise la session DB du parent. learn=False : pas d'auto-apprentissage (ex. résolution par timeout)."""
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule:
                return

            now = datetime.utcnow()
            duration_str = "N/A"
            if rule_id in self._alert_states:
                started_at = self._alert_states[rule_id]["started_at"]
                if started_at:
                    duration = now - started_at
                    hours, remainder = divmod(int(duration.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"{hours}h {minutes}m"
                    else:
                        duration_str = f"{minutes}m {seconds}s"

            self._alert_states[rule_id] = {
                "status": "normal",
                "started_at": None,
                "last_error_at": None,
            }

            rule.alert_status = "normal"
            rule.alert_started_at = None
            
            pending_analyses = db.query(Analysis).filter(
                Analysis.rule_id == rule_id,
                Analysis.resolution_status == None
            ).all()
            error_lines = [l for a in pending_analyses for l in analysis_lines(a)]
            for analysis in pending_analyses:
                analysis.resolved_at = now
                analysis.resolution_status = "resolved"
                analysis.resolution_line = resolution_line
                analysis.resolution_patterns_json = json.dumps(resolution_patterns or [])
                analysis.resolution_ai_explanation = ai_explanation
                analysis.resolution_ai_confidence = confidence

            db.commit()
            logger.info("ResolutionService", f"La règle '{rule.name}' est résolue avec succès !")

            if rule.resolution_notify_resolved:
                await self._send_notification_for(db, rule, "resolved",
                                                  duration=duration_str, trigger=trigger,
                                                  explanation=ai_explanation, confidence=confidence)

            if learn and context_lines and rule.resolution_learn and (rule.resolution_ai_mode or "ambiguous") != "never":
                asyncio.create_task(self._extract_resolution_keywords(rule_id, context_lines, error_lines))

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors du marquage comme résolu : {e}")

    async def mark_resolved_manually(self, rule_id: int):
        """Resolution manuelle par l'utilisateur. Skip la validation IA."""
        context_lines = []
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule and rule.log_file_path:
                resolved_path = rule.log_file_path
                if rule.log_file_path.startswith("[WEBHOOK]:"):
                    token = rule.log_file_path.split(':', 1)[1]
                    safe = "".join(c for c in token if c.isalnum() or c in "-_")
                    resolved_path = os.path.join(os.environ.get("SENTINEL_DATA_DIR", "/app/data"), "webhooks", f"{safe}.log")
                elif rule.log_file_path.startswith("[SYSLOG]:"):
                    hostname = rule.log_file_path.split(':', 1)[1]
                    safe = "".join(c for c in hostname if c.isalnum() or c in "-_")
                    resolved_path = os.path.join(os.environ.get("SENTINEL_DATA_DIR", "/app/data"), "syslog", f"{safe}.log")
                context_lines = _read_tail_lines(resolved_path, n=30)
        finally:
            db.close()

        async with self._state_lock:
            db = SessionLocal()
            try:
                max_severity = self._alert_states.get(rule_id, {}).get("max_severity", "info")
                self._record_verdict(db, rule_id, trigger="Manual override", outcome="manual",
                                     max_severity=max_severity, context_lines=context_lines,
                                     resolution_line="Resolution manuelle par l'utilisateur",
                                     resolution_patterns=[])
                await self._mark_resolved(db, rule_id,
                                          trigger="Manual override",
                                          ai_explanation="Resolu manuellement par l'utilisateur.",
                                          context_lines=context_lines,
                                          resolution_line="Resolution manuelle par l'utilisateur",
                                          resolution_patterns=[])
            finally:
                db.close()

    async def _extract_resolution_keywords(self, rule_id: int, context_lines: List[str], error_lines: List[str] = None):
        """Apres validation IA ou override, propose de nouveaux patterns de resolution (poids 1).
        Les candidats generiques, presents dans les lignes d'erreur de l'incident ou absents des logs sont refuses ;
        le poids d'un pattern n'augmente que lorsqu'il declenche une resolution validee (voir _try_resolve)."""
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule:
                return

            config = db.query(GlobalConfig).first()
            if not config:
                return

            # Recuperation des mots-cles d'erreur actifs pour contextualiser les patterns
            error_keywords = rule.get_keywords()

            # Seules les lignes normales sont proposees au LLM : une ligne d'erreur ne signale pas un retour a la normale
            excluded = rule.get_excluded_patterns()
            normal_lines = [l for l in context_lines if not _is_error_line(l, error_keywords, excluded)]
            recent_logs = "\n".join(normal_lines[-30:])
            if not recent_logs:
                return

            lang = config.ollama_prompt_lang or "fr"
            if lang == "en":
                prompt = f"""You are an auto-learning assistant for log monitoring.
A rule named '{rule.name}' just returned to normal.
The logs that show the resolution are:
{recent_logs}

Identify 1 to 3 distinct keywords or short key phrases (case insensitive, e.g., "connection established", "restored") that clearly signal the return to normal or success in these logs.
Format your response as a JSON array of strings, for example: ["keyword1", "keyword2"]
Return ONLY the raw JSON array, without markdown formatting or code blocks.
"""
            else:
                prompt = f"""Tu es un assistant d'auto-apprentissage pour la surveillance des logs.
Une regle nommee '{rule.name}' vient de repasser a la normale.
Les logs montrant le retour a la normale sont :
{recent_logs}

Identifie 1 a 3 mots-cles ou courtes expressions distinctes (insensibles a la casse, ex: "connection established", "restored") qui signalent clairement le retour a la normale ou le succes dans ces logs.
Formate ta reponse sous forme d'un tableau JSON de chaines de caracteres, par exemple : ["keyword1", "keyword2"]
Retourne UNIQUEMENT le tableau JSON brut, sans formatage markdown ni bloc de code.
"""

            logger.debug("ResolutionService", f"Extraction IA de mots-cles de resolution pour la regle '{rule.name}'...")
            
            async with self.orchestrator._ollama_semaphore:
                try:
                    response = await asyncio.wait_for(
                        self.orchestrator.ollama.analyze_async(
                            prompt=prompt,
                            url=config.ollama_url,
                            model=config.ollama_model,
                            think=False,
                            options=llm_options(config_to_dict(config), temperature=0.2)
                        ),
                        timeout=90.0
                    )
                except asyncio.TimeoutError:
                    return

            cleaned = clean_ollama_json(response)

            try:
                new_patterns = json.loads(cleaned)
                if isinstance(new_patterns, list):
                    from app.services import autopilot
                    autonomous = autopilot.enabled(rule)
                    known = {p.get("pattern", "").lower() for p in rule.get_weighted_resolution_patterns()}
                    pending = sum(1 for p in rule.get_weighted_resolution_patterns() if p.get("status") == "suggested")
                    learned_active = sum(1 for p in rule.get_weighted_resolution_patterns()
                                         if p.get("status", "active") == "active" and p.get("origin") in ("learned", "autopilot"))
                    recent = await asyncio.to_thread(_tail, rule.log_file_path, 5000) if autonomous else []
                    added = []
                    for pat in new_patterns:
                        pat_cleaned = str(pat).strip()
                        if not pat_cleaned or pat_cleaned.lower() in known:
                            continue
                        if (learned_active if autonomous else pending) + len(added) >= \
                                (autopilot.AUTOPILOT_PATTERNS_MAX if autonomous else MAX_PENDING_SUGGESTIONS):
                            continue
                        if not _is_learnable_pattern(pat_cleaned, error_keywords, error_lines or [], normal_lines):
                            logger.debug("ResolutionService", f"Pattern appris refuse pour '{rule.name}' : '{pat_cleaned}'")
                            continue
                        if autonomous:
                            # Pilote automatique : appliqué directement s'il passe aussi les garde-fous sur l'historique
                            if not autopilot.pattern_is_safe(db, rule, pat_cleaned, recent):
                                logger.debug("ResolutionService", f"Motif écarté par le pilote pour '{rule.name}' : '{pat_cleaned}'")
                                continue
                            rule.set_pattern_status(pat_cleaned, "active", origin="autopilot")
                            added.append(pat_cleaned)
                        # Sinon : suggestion en attente de validation par l'utilisateur
                        elif rule.suggest_pattern(pat_cleaned, error_keywords=error_keywords):
                            added.append(pat_cleaned)
                    if added:
                        db.commit()
                        logger.info("ResolutionService", f"Auto-apprentissage: {len(added)} motif(s) "
                                    f"{'appliqué(s) par le pilote' if autonomous else 'proposé(s)'} pour '{rule.name}': {added}")
            except Exception as e:
                logger.error("ResolutionService", f"Erreur parsing mots-cles d'apprentissage: {e}. Contenu: {cleaned}")

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de l'auto-apprentissage de resolution : {e}")
        finally:
            db.close()

    def reopen_incident(self, db, rule: Rule, resolved_since: datetime):
        """Annule une resolution jugee fausse par l'utilisateur : la regle repasse en alerte avec les analyses
        que cette resolution avait cloturees (resolved_at dans les 2 minutes suivant le verdict). Commit par l'appelant."""
        reopened = db.query(Analysis).filter(
            Analysis.rule_id == rule.id,
            Analysis.resolution_status == "resolved",
            Analysis.resolved_at >= resolved_since,
            Analysis.resolved_at <= resolved_since + timedelta(minutes=2),
        ).all()
        for a in reopened:
            a.resolved_at = None
            a.resolution_status = None
            a.resolution_line = None
            a.resolution_patterns_json = "[]"
            a.resolution_ai_explanation = None
            a.resolution_ai_confidence = None
        now = datetime.utcnow()
        started = min((a.analyzed_at for a in reopened if a.analyzed_at), default=now)
        rule.alert_status = "alert"
        rule.alert_started_at = started
        db.flush()
        self._alert_states[rule.id] = {
            "status": "alert",
            "started_at": started,
            "last_error_at": now,  # le delai d'inactivite repart de maintenant
            "max_severity": self._get_max_severity_for_rule(rule.id, db),
            "lines_since_error": None,
            "error_times": sorted(a.analyzed_at for a in reopened if a.analyzed_at)[-MAX_ERROR_TIMES:],
        }
        logger.info("ResolutionService", f"Resolution annulee (faux positif) pour '{rule.name}' : {len(reopened)} analyse(s) rouverte(s)")

    def purge_old_verdicts(self):
        """Supprime les verdicts de plus de VERDICT_RETENTION_DAYS jours, en gardant les VERDICT_KEEP_PER_RULE plus recents par regle."""
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=VERDICT_RETENTION_DAYS)
            removed = 0
            for (rule_id,) in db.query(ResolutionVerdict.rule_id).distinct().all():
                keep_ids = [vid for (vid,) in db.query(ResolutionVerdict.id).filter(
                    ResolutionVerdict.rule_id == rule_id
                ).order_by(ResolutionVerdict.created_at.desc()).limit(VERDICT_KEEP_PER_RULE).all()]
                q = db.query(ResolutionVerdict).filter(ResolutionVerdict.rule_id == rule_id, ResolutionVerdict.created_at < cutoff)
                if keep_ids:
                    q = q.filter(~ResolutionVerdict.id.in_(keep_ids))
                removed += q.delete(synchronize_session=False)
            db.commit()
            if removed:
                logger.info("ResolutionService", f"Purge : {removed} verdict(s) de resolution de plus de {VERDICT_RETENTION_DAYS} jours supprime(s)")
            return removed
        except Exception as e:
            logger.error("ResolutionService", f"Erreur dans purge_old_verdicts : {e}")
            return 0
        finally:
            db.close()

    # ── Notifications unifiées ──────────────────────────────────────────────

    async def _send_notification_for(self, db, rule: Rule, notif_type: str, **kwargs):
        """Point d'entree unique pour les notifications de resolution.
        Filtre selon notify_severity_threshold (Option 1 : filtrage total)."""
        try:
            # Garde : filtrage par severite avant tout envoi
            if not self._should_notify_for_rule(db, rule):
                return

            config = db.query(GlobalConfig).first()
            if not config or not self.orchestrator:
                return

            lang = config.site_lang or "fr"
            instance_prefix = f"[{config.instance_name}] " if config.instance_name else ""
            notif_config = _get_notification_config(config)

            if notif_type == "resolved":
                duration = kwargs.get("duration", "N/A")
                trigger = kwargs.get("trigger", "")
                explanation = kwargs.get("explanation")
                confidence = kwargs.get("confidence")

                subject = f"{instance_prefix}{nt('resolution_resolved_subject', lang).format(rule_name=rule.name)}"
                body = f"""
                <h2>🟢 {nt('resolution_resolved_subject', lang).format(rule_name=html.escape(rule.name or ''))}</h2>
                <p>{nt('resolution_resolved_body', lang).format(rule_name=html.escape(rule.name or ''), duration=duration)}</p>
                <p><strong>{nt('trigger', lang)}:</strong> {html.escape(str(trigger))}</p>
                """
                if explanation:
                    body += f"<p><strong>{nt('ollama_analysis', lang)}:</strong> {html.escape(str(explanation))} ({confidence}% confidence)</p>"
                
                if config.notification_method in ("apprise", "discord"):
                    body = f"🟢 **{nt('resolution_resolved_subject', lang).format(rule_name=rule.name)}**\n\n{nt('resolution_resolved_body', lang).format(rule_name=rule.name, duration=duration)}\n\n**{nt('trigger', lang)}:** {trigger}"
                    if explanation:
                        body += f"\n**{nt('ollama_analysis', lang)}:** {explanation} ({confidence}% confidence)"
            else:
                logger.warning("ResolutionService", f"Type de notification inconnu : {notif_type}")
                return

            await asyncio.to_thread(self.orchestrator.notifier.send, subject, body, notif_config)

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de l'envoi de notification ({notif_type}) : {e}")

    # ── Option 1 : Decay temporel des patterns ──────────────────────────────

    async def decay_stale_patterns(self):
        """Decremente le poids des patterns non valides depuis PATTERN_DECAY_DAYS jours.
        Les patterns dont le poids tombe a 0 sont supprimes automatiquement.
        Appele periodiquement par le scheduler (toutes les PATTERN_DECAY_INTERVAL_HOURS heures)."""
        db = SessionLocal()
        try:
            if monitoring.is_paused(db):  # rien n'a été observé : un motif n'est pas périmé pour autant
                return
            rules = db.query(Rule).filter(Rule.resolution_mode != "disabled").all()
            total_decayed = 0
            total_removed = 0

            for rule in rules:
                weighted = rule.get_weighted_resolution_patterns()
                if not weighted:
                    continue

                updated = []
                decayed = 0
                removed = 0

                for item in weighted:
                    last_validated = item.get("last_validated_at")
                    if last_validated:
                        try:
                            last_dt = datetime.fromisoformat(last_validated.replace("Z", "+00:00")).replace(tzinfo=None)
                        except (ValueError, AttributeError):
                            last_dt = None
                    else:
                        last_dt = None

                    if last_dt is None:
                        # Pattern sans date : initialiser a maintenant (migration)
                        item["last_validated_at"] = datetime.utcnow().isoformat() + "Z"
                        updated.append(item)
                        continue

                    age_days = (datetime.utcnow() - last_dt).total_seconds() / 86400
                    if age_days > PATTERN_DECAY_DAYS:
                        new_weight = item.get("weight", 1) - 1
                        if new_weight > 0:
                            item["weight"] = new_weight
                            updated.append(item)
                            decayed += 1
                        else:
                            removed += 1
                            # Ne pas ajouter a updated = suppression effective
                    else:
                        updated.append(item)

                if decayed > 0 or removed > 0:
                    rule.set_weighted_resolution_patterns(updated)
                    total_decayed += decayed
                    total_removed += removed
                    logger.info("ResolutionService", f"Decay patterns pour '{rule.name}': {decayed} decremente(s), {removed} supprime(s)")

            if total_decayed > 0 or total_removed > 0:
                db.commit()
                logger.info("ResolutionService", f"Decay total : {total_decayed} decremente(s), {total_removed} supprime(s) sur {len(rules)} regle(s)")
        except Exception as e:
            logger.error("ResolutionService", f"Erreur dans decay_stale_patterns : {e}")
        finally:
            db.close()

    # ── Option 3 : Audit IA periodique des patterns ─────────────────────────

    async def audit_patterns_with_ai(self, rule_id: int) -> dict:
        """Demande au LLM d'evaluer la pertinence de chaque pattern de resolution
        d'une regle, par rapport a son contexte applicatif et ses mots-cles d'erreur.
        Retourne un dict {"kept": [...], "removed": [...], "explanation": "..."}."""
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule:
                return {"error": "rule_not_found"}

            config = db.query(GlobalConfig).first()
            if not config:
                return {"error": "no_config"}

            weighted = rule.get_weighted_resolution_patterns()
            if not weighted:
                return {"kept": [], "removed": [], "explanation": "Aucun pattern a auditer."}

            error_keywords = rule.get_keywords()
            patterns_desc = "\n".join([
                f"  - \"{item['pattern']}\" (poids={item.get('weight', 1)}, derniere validation={item.get('last_validated_at', 'jamais')}, mots-cles d'erreur associes={item.get('error_keywords', [])})"
                for item in weighted
            ])

            # Lire quelques lignes recentes du fichier log pour le contexte (BUG-RES-04)
            # Les chemins virtuels [SYSLOG]:hostname et [WEBHOOK]:token sont resolus vers le fichier reel
            recent_logs = ""
            if rule.log_file_path:
                log_path = rule.log_file_path
                if rule.log_file_path.startswith("[SYSLOG]:"):
                    hostname = rule.log_file_path.split(":", 1)[1].strip()
                    safe = "".join(c for c in hostname if c.isalnum() or c in "-_")
                    log_path = os.path.join(os.environ.get("SENTINEL_DATA_DIR", "/app/data"), "syslog", f"{safe}.log")
                elif rule.log_file_path.startswith("[WEBHOOK]:"):
                    token = rule.log_file_path.split(":", 1)[1].strip()
                    safe = "".join(c for c in token if c.isalnum() or c in "-_")
                    log_path = os.path.join(os.environ.get("SENTINEL_DATA_DIR", "/app/data"), "webhooks", f"{safe}.log")
                lines = _read_tail_lines(log_path, n=30)
                if lines:
                    recent_logs = f"\nLogs recents (dernieres 30 lignes):\n" + "\n".join(lines[-30:])

            lang = config.ollama_prompt_lang or "fr"
            if lang == "en":
                prompt = f"""You are auditing the resolution patterns of a log monitoring rule.
Rule name: {rule.name}
Application context: {rule.application_context or 'N/A'}
Error keywords: {', '.join(error_keywords) if error_keywords else 'N/A'}

Current resolution patterns:
{patterns_desc}
{recent_logs}

For each pattern, determine if it is:
- RELEVANT: clearly indicates the issue is resolved and matches the application context
- IRRELEVANT: too generic (e.g., "OK", "success"), unrelated to this rule's context, or obsolete

Return a JSON object with:
- "keep": list of pattern strings to keep
- "remove": list of pattern strings to remove
- "explanation": brief summary of your reasoning

Return ONLY the raw JSON object, without markdown formatting or code blocks.
"""
            else:
                prompt = f"""Tu audites les patterns de resolution d'une regle de surveillance de logs.
Nom de la regle: {rule.name}
Contexte applicatif: {rule.application_context or 'N/A'}
Mots-cles d'erreur: {', '.join(error_keywords) if error_keywords else 'N/A'}

Patterns de resolution actuels:
{patterns_desc}
{recent_logs}

Pour chaque pattern, determine s'il est :
- PERTINENT : indique clairement que le probleme est resolu, et correspond au contexte applicatif
- NON PERTINENT : trop generique (ex: "OK", "success"), sans rapport avec cette regle, ou obsolete

Retourne un objet JSON avec :
- "keep": liste des patterns (strings) a conserver
- "remove": liste des patterns (strings) a supprimer
- "explanation": bref resume de ton raisonnement

Retourne UNIQUEMENT l'objet JSON brut, sans formatage markdown ni bloc de code.
"""

            logger.info("ResolutionService", f"Audit IA des patterns pour '{rule.name}' ({len(weighted)} patterns)...")

            async with self.orchestrator._ollama_semaphore:
                try:
                    response = await asyncio.wait_for(
                        self.orchestrator.ollama.analyze_async(
                            prompt=prompt,
                            url=config.ollama_url,
                            model=config.ollama_model,
                            think=False,
                            options=llm_options(config_to_dict(config), temperature=0.3)
                        ),
                        timeout=120.0
                    )
                except asyncio.TimeoutError:
                    return {"error": "timeout", "explanation": "L'IA n'a pas repondu dans le delai imparti."}

            cleaned = clean_ollama_json(response)
            try:
                result = json.loads(cleaned)
                keep = result.get("keep", [])
                remove = result.get("remove", [])
                explanation = result.get("explanation", "")

                # Appliquer les suppressions
                removed_patterns = []
                if remove:
                    for pat in remove:
                        pat_str = str(pat).strip()
                        if pat_str:
                            rule.remove_pattern(pat_str)
                            removed_patterns.append(pat_str)

                if removed_patterns:
                    db.commit()
                    logger.info("ResolutionService", f"Audit IA : {len(removed_patterns)} pattern(s) supprime(s) pour '{rule.name}': {removed_patterns}")

                return {
                    "kept": keep,
                    "removed": removed_patterns,
                    "explanation": explanation,
                    "total_before": len(weighted),
                    "total_after": len(weighted) - len(removed_patterns),
                }
            except Exception as e:
                logger.error("ResolutionService", f"Erreur parsing audit IA : {e}. Contenu: {cleaned}")
                return {"error": "parse_error", "raw": cleaned[:500]}

        except Exception as e:
            logger.error("ResolutionService", f"Erreur dans audit_patterns_with_ai : {e}")
            return {"error": str(e)}
        finally:
            db.close()
