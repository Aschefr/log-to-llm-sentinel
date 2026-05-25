import asyncio
import json
import os
import collections
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.utils.notification_i18n import nt
from app import logger


# ── Seuil de confiance IA minimum pour accepter une résolution ──
AI_CONFIDENCE_THRESHOLD = 50


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


class ResolutionService:
    """Détecte et valide le retour à la normale des règles en alerte."""

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        # Structure de _alert_states:
        # rule_id -> {
        #     "status": str ("normal", "alert", "resolving"),
        #     "started_at": datetime,
        #     "last_error_at": datetime,
        # }
        self._alert_states = {}
        self._state_lock = asyncio.Lock()

    def set_orchestrator(self, orchestrator):
        self.orchestrator = orchestrator

    def restore_states_from_db(self):
        """Restaure les états d'alerte depuis la base de données au démarrage."""
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.alert_status != "normal").all()
            for rule in rules:
                self._alert_states[rule.id] = {
                    "status": rule.alert_status or "normal",
                    "started_at": rule.alert_started_at,
                    "last_error_at": rule.alert_started_at,  # Fallback
                }
                logger.info("ResolutionService", f"État d'alerte restauré pour la règle '{rule.name}': {rule.alert_status}")
        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de la restauration des états d'alerte : {e}")
        finally:
            db.close()

    async def on_error_detected(self, rule_id: int):
        """Appelé par l'Orchestrateur quand un match d'erreur est détecté.
        Passe la règle en état 'alert' si elle ne l'est pas déjà."""
        async with self._state_lock:
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    return

                # Garde explicite : si la résolution est désactivée, ne pas gérer les états
                if rule.resolution_mode == "disabled":
                    return

                now = datetime.utcnow()
                if rule_id not in self._alert_states or self._alert_states[rule_id]["status"] == "normal":
                    self._alert_states[rule_id] = {
                        "status": "alert",
                        "started_at": now,
                        "last_error_at": now,
                    }
                    rule.alert_status = "alert"
                    rule.alert_started_at = now
                    db.commit()
                    logger.info("ResolutionService", f"La règle '{rule.name}' passe en état ALERT")
                else:
                    self._alert_states[rule_id]["status"] = "alert"
                    self._alert_states[rule_id]["last_error_at"] = now
                    if rule.alert_status != "alert":
                        rule.alert_status = "alert"
                        db.commit()
                    logger.debug("ResolutionService", f"Erreur répétée pour la règle '{rule.name}', mise à jour last_error_at")
            except Exception as e:
                logger.error("ResolutionService", f"Erreur dans on_error_detected : {e}")
            finally:
                db.close()

    async def on_new_lines(self, rule: Rule, lines: List[str]):
        """Appelé pour chaque lot de nouvelles lignes (toutes, pas seulement les matchs).
        Vérifie si un pattern de résolution est détecté."""
        # Garde explicite : si la résolution est désactivée, on ne vérifie rien
        if rule.resolution_mode == "disabled":
            return

        state = self._alert_states.get(rule.id)
        if not state or state["status"] not in ("alert", "resolving"):
            return

        if rule.resolution_mode not in ("pattern", "both"):
            return

        patterns = rule.get_resolution_patterns()
        if not patterns:
            return

        matched_line = None
        matched_pattern = None
        for line in lines:
            if len(line) > 10_000:
                continue
            for pat in patterns:
                if pat.lower() in line.lower():
                    matched_line = line
                    matched_pattern = pat
                    break
            if matched_line:
                break

        if matched_line:
            logger.info("ResolutionService", f"Pattern de résolution '{matched_pattern}' détecté dans la ligne: {matched_line[:120]}")
            await self._try_resolve(rule.id, trigger=f"Pattern match: '{matched_pattern}'", context_lines=lines, resolution_line=matched_line, resolution_patterns=[matched_pattern])

    async def check_timeout_resolutions(self):
        """Boucle périodique — vérifie si le timeout d'absence d'erreur est atteint."""
        db = SessionLocal()
        try:
            for rule_id, state in list(self._alert_states.items()):
                if state["status"] not in ("alert", "resolving"):
                    continue

                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule or not rule.enabled:
                    continue

                # Garde explicite
                if rule.resolution_mode == "disabled":
                    continue

                if rule.resolution_mode not in ("timeout", "both"):
                    continue

                timeout_mins = rule.resolution_timeout_minutes or 30
                last_err = state["last_error_at"]

                if last_err and (datetime.utcnow() - last_err >= timedelta(minutes=timeout_mins)):
                    logger.info("ResolutionService", f"Timeout de résolution atteint ({timeout_mins} min) pour la règle '{rule.name}'")
                    await self._try_resolve(rule_id, trigger=f"Timeout ({timeout_mins} min)", context_lines=[], resolution_line=f"Inactivité de l'erreur supérieure à {timeout_mins} minutes", resolution_patterns=[])
        except Exception as e:
            logger.error("ResolutionService", f"Erreur dans check_timeout_resolutions : {e}")
        finally:
            db.close()

    async def _try_resolve(self, rule_id: int, trigger: str, context_lines: List[str], resolution_line: str = None, resolution_patterns: List[str] = None):
        """Tente la résolution avec validation IA optionnelle.
        Utilise une session DB unique pour toute la transaction."""
        async with self._state_lock:
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    return

                self._alert_states[rule_id]["status"] = "resolving"
                rule.alert_status = "resolving"
                db.commit()

                if rule.resolution_ai_enabled:
                    if rule.resolution_notify_search:
                        await self._send_notification_for(db, rule, "search", trigger=trigger)

                    verdict = await self._validate_with_ai(db, rule, trigger, context_lines)

                    # Seuil de confiance : rejeter si la confiance est trop basse
                    confidence = verdict.get("confidence", 0)
                    if verdict.get("resolved") and confidence < AI_CONFIDENCE_THRESHOLD:
                        logger.info("ResolutionService",
                                    f"Résolution acceptée par l'IA mais confiance trop basse ({confidence}% < {AI_CONFIDENCE_THRESHOLD}%) pour '{rule.name}'. Rejet.")
                        verdict["resolved"] = False
                        verdict["explanation"] = f"{verdict.get('explanation', '')} [Rejeté : confiance {confidence}% < seuil {AI_CONFIDENCE_THRESHOLD}%]"

                    if verdict.get("resolved"):
                        logger.info("ResolutionService", f"Résolution validée par l'IA pour la règle '{rule.name}' avec {confidence}% de confiance.")
                        await self._mark_resolved(db, rule_id, trigger,
                                                  ai_explanation=verdict.get("explanation"),
                                                  confidence=confidence,
                                                  context_lines=context_lines,
                                                  resolution_line=resolution_line,
                                                  resolution_patterns=resolution_patterns)
                    else:
                        logger.info("ResolutionService", f"Résolution rejetée par l'IA pour la règle '{rule.name}'. Explication: {verdict.get('explanation')}")
                        self._alert_states[rule_id]["status"] = "alert"
                        rule.alert_status = "alert"
                        db.commit()
                        if rule.resolution_notify_search:
                            await self._send_notification_for(db, rule, "denied")
                else:
                    await self._mark_resolved(db, rule_id, trigger, context_lines=context_lines,
                                              resolution_line=resolution_line, resolution_patterns=resolution_patterns)
            except Exception as e:
                logger.error("ResolutionService", f"Erreur dans _try_resolve pour rule_id {rule_id} : {e}")
                if rule_id in self._alert_states:
                    self._alert_states[rule_id]["status"] = "alert"
            finally:
                db.close()

    async def _validate_with_ai(self, db, rule: Rule, trigger: str, context_lines: List[str]) -> dict:
        """Envoie à Ollama le contexte pour obtenir un verdict de retour à la normale.
        Réutilise la session DB du parent pour éviter les ouvertures multiples."""
        try:
            config = db.query(GlobalConfig).first()
            if not config:
                return {"resolved": False, "confidence": 0, "explanation": "Pas de configuration globale"}

            lang = config.ollama_prompt_lang or "fr"
            last_analysis = db.query(Analysis).filter(Analysis.rule_id == rule.id).order_by(Analysis.analyzed_at.desc()).first()
            error_details = last_analysis.triggered_line if last_analysis else "Inconnue"
            error_analysis = last_analysis.ollama_response if last_analysis else "Inconnue"

            recent_logs = "\n".join(context_lines[-20:]) if context_lines else "Pas de logs récents (Timeout)"

            if lang == "en":
                prompt = f"""You are checking if a system issue has resolved.
Rule name: {rule.name}
Application context: {rule.application_context}
Initial triggering error: {error_details}
Error analysis: {error_analysis}
Trigger for resolution check: {trigger}
Recent logs to check:
{recent_logs}

Determine if the situation has returned to normal (resolved: true) or if the issue is still ongoing (resolved: false).
Format your response as a JSON object with keys:
- "resolved": boolean
- "confidence": integer (0 to 100)
- "explanation": string containing a short explanation in English

Return ONLY the raw JSON object, without markdown formatting or code blocks.
"""
            else:
                prompt = f"""Tu dois vérifier si un problème système est résolu et si la situation est revenue à la normale.
Nom de la règle: {rule.name}
Contexte applicatif: {rule.application_context}
Erreur déclenchante initiale: {error_details}
Analyse initiale de l'erreur: {error_analysis}
Déclencheur de la vérification de résolution: {trigger}
Logs récents à examiner:
{recent_logs}

Détermine si la situation est revenue à la normale (resolved: true) ou si le problème persiste (resolved: false).
Formate ta réponse sous forme d'un objet JSON avec les clés :
- "resolved": boolean (true/false)
- "confidence": entier (de 0 à 100)
- "explanation": string contenant une courte explication en français

Retourne UNIQUEMENT l'objet JSON brut, sans formatage markdown ni bloc de code.
"""

            if not self.orchestrator:
                return {"resolved": False, "confidence": 0, "explanation": "Orchestrateur non injecté"}

            logger.debug("ResolutionService", f"Appel Ollama pour validation de résolution de '{rule.name}'...")
            
            async with self.orchestrator._ollama_semaphore:
                try:
                    response = await asyncio.wait_for(
                        self.orchestrator.ollama.analyze_async(
                            prompt=prompt,
                            url=config.ollama_url,
                            model=config.ollama_model,
                            think=config.ollama_think,
                            options={
                                "temperature": 0.1,
                                "num_ctx": 4096
                            }
                        ),
                        timeout=180.0
                    )
                except asyncio.TimeoutError:
                    response = '{"resolved": false, "confidence": 0, "explanation": "Timeout Ollama (180s)"}'

            logger.debug("ResolutionService", f"Réponse Ollama brute: {response}")
            
            cleaned = clean_ollama_json(response)

            try:
                data = json.loads(cleaned)
                return {
                    "resolved": bool(data.get("resolved", False)),
                    "confidence": int(data.get("confidence", 0)),
                    "explanation": str(data.get("explanation", "Aucune explication fournie"))
                }
            except Exception as e:
                logger.error("ResolutionService", f"Erreur de parsing JSON de la réponse Ollama: {e}. Contenu: {cleaned}")
                lower_resp = response.lower()
                if '"resolved": true' in lower_resp or '"resolved":true' in lower_resp:
                    return {"resolved": True, "confidence": 70, "explanation": "Validation (parsing fallback)"}
                return {"resolved": False, "confidence": 0, "explanation": f"Erreur parsing réponse : {response[:100]}"}

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de la validation IA : {e}")
            return {"resolved": False, "confidence": 0, "explanation": str(e)}

    async def _mark_resolved(self, db, rule_id: int, trigger: str, ai_explanation: str = None, confidence: int = None, context_lines: List[str] = None, resolution_line: str = None, resolution_patterns: List[str] = None):
        """Passe la règle en 'normal', met à jour la BDD, et envoie la notification.
        Réutilise la session DB du parent."""
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

            if rule.resolution_ai_enabled and context_lines:
                asyncio.create_task(self._extract_resolution_keywords(rule_id, context_lines))

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors du marquage comme résolu : {e}")

    async def mark_resolved_manually(self, rule_id: int):
        """Résolution manuelle par l'utilisateur. Skip la validation IA, mais extrait les mots-clés en arrière-plan."""
        context_lines = []
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if rule and rule.log_file_path and not rule.log_file_path.startswith("[WEBHOOK]:"):
                context_lines = _read_tail_lines(rule.log_file_path, n=30)
        finally:
            db.close()

        # _try_resolve gère sa propre session DB et son propre lock
        # Pour la résolution manuelle, on va directement dans _mark_resolved avec lock
        async with self._state_lock:
            db = SessionLocal()
            try:
                await self._mark_resolved(db, rule_id,
                                          trigger="Manual override",
                                          ai_explanation="Résolu manuellement par l'utilisateur.",
                                          context_lines=context_lines,
                                          resolution_line="Résolution manuelle par l'utilisateur",
                                          resolution_patterns=[])
            finally:
                db.close()

    async def _extract_resolution_keywords(self, rule_id: int, context_lines: List[str]):
        """Après validation IA ou override, extrait les patterns de résolution."""
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule:
                return

            config = db.query(GlobalConfig).first()
            if not config:
                return

            recent_logs = "\n".join(context_lines[-30:]) if context_lines else ""
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
Une règle nommée '{rule.name}' vient de repasser à la normale.
Les logs montrant le retour à la normale sont :
{recent_logs}

Identifie 1 à 3 mots-clés ou courtes expressions distinctes (insensibles à la casse, ex: "connection established", "restored") qui signalent clairement le retour à la normale ou le succès dans ces logs.
Formate ta réponse sous forme d'un tableau JSON de chaînes de caractères, par exemple : ["keyword1", "keyword2"]
Retourne UNIQUEMENT le tableau JSON brut, sans formatage markdown ni bloc de code.
"""

            logger.debug("ResolutionService", f"Extraction IA de mots-clés de résolution pour la règle '{rule.name}'...")
            
            async with self.orchestrator._ollama_semaphore:
                try:
                    response = await asyncio.wait_for(
                        self.orchestrator.ollama.analyze_async(
                            prompt=prompt,
                            url=config.ollama_url,
                            model=config.ollama_model,
                            think=False,
                            options={
                                "temperature": 0.2,
                                "num_ctx": 4096
                            }
                        ),
                        timeout=90.0
                    )
                except asyncio.TimeoutError:
                    return

            cleaned = clean_ollama_json(response)

            try:
                new_patterns = json.loads(cleaned)
                if isinstance(new_patterns, list):
                    current_patterns = rule.get_resolution_patterns()
                    added = []
                    for pat in new_patterns:
                        pat_cleaned = str(pat).strip()
                        if pat_cleaned and pat_cleaned.lower() not in [p.lower() for p in current_patterns]:
                            current_patterns.append(pat_cleaned)
                            added.append(pat_cleaned)
                    if added:
                        rule.set_resolution_patterns(current_patterns)
                        db.commit()
                        logger.info("ResolutionService", f"Auto-apprentissage: {len(added)} nouveau(x) pattern(s) de résolution ajouté(s) à '{rule.name}': {added}")
            except Exception as e:
                logger.error("ResolutionService", f"Erreur parsing mots-clés d'apprentissage: {e}. Contenu: {cleaned}")

        except Exception as e:
            logger.error("ResolutionService", f"Erreur lors de l'auto-apprentissage de résolution : {e}")
        finally:
            db.close()

    # ── Notifications unifiées ──────────────────────────────────────────────

    async def _send_notification_for(self, db, rule: Rule, notif_type: str, **kwargs):
        """Point d'entrée unique pour l'envoi de notifications de résolution.
        notif_type: 'search' | 'denied' | 'resolved'
        Réutilise la session DB du parent et le helper _get_notification_config."""
        try:
            config = db.query(GlobalConfig).first()
            if not config or not self.orchestrator:
                return

            lang = config.site_lang or "fr"
            instance_prefix = f"[{config.instance_name}] " if config.instance_name else ""
            notif_config = _get_notification_config(config)

            if notif_type == "search":
                trigger = kwargs.get("trigger", "")
                subject = f"{instance_prefix}{nt('resolution_ai_searching', lang).format(rule_name=rule.name)}"
                body = f"""
                <h3>🔍 {nt('resolution_ai_searching', lang).format(rule_name=rule.name)}</h3>
                <p><strong>{nt('rule', lang)}:</strong> {rule.name}</p>
                <p><strong>{nt('trigger', lang)}:</strong> {trigger}</p>
                """
                if config.notification_method in ("apprise", "discord"):
                    body = f"🔍 {nt('resolution_ai_searching', lang).format(rule_name=rule.name)}\n\n**{nt('trigger', lang)}:** {trigger}"

            elif notif_type == "denied":
                subject = f"{instance_prefix}[Sentinel] {nt('resolution_ai_denied', lang)}"
                body = f"""
                <h3>⚠️ {nt('resolution_ai_denied', lang)}</h3>
                <p><strong>{nt('rule', lang)}:</strong> {rule.name}</p>
                """
                if config.notification_method in ("apprise", "discord"):
                    body = f"⚠️ **[Sentinel]** {nt('resolution_ai_denied', lang)} ({rule.name})"

            elif notif_type == "resolved":
                duration = kwargs.get("duration", "N/A")
                trigger = kwargs.get("trigger", "")
                explanation = kwargs.get("explanation")
                confidence = kwargs.get("confidence")

                subject = f"{instance_prefix}{nt('resolution_resolved_subject', lang).format(rule_name=rule.name)}"
                body = f"""
                <h2>🟢 {nt('resolution_resolved_subject', lang).format(rule_name=rule.name)}</h2>
                <p>{nt('resolution_resolved_body', lang).format(rule_name=rule.name, duration=duration)}</p>
                <p><strong>{nt('trigger', lang)}:</strong> {trigger}</p>
                """
                if explanation:
                    body += f"<p><strong>{nt('ollama_analysis', lang)}:</strong> {explanation} ({confidence}% confidence)</p>"
                
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
