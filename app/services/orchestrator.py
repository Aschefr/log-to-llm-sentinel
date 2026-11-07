import asyncio
import html
import uuid
import json
from typing import List, Optional
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services import analysis_events, monitoring
from app.services.ollama_service import OllamaService
from app.services.notification_service import NotificationService
from app.services.app_config import config_to_dict, llm_options
from app.services import event_notes
from app.services.alert_analysis import (build_history, build_alert_messages, messages_to_text,
                                         parse_alert_response, detect_severity, guard_log_instruction, analysis_lines)
from app.services.log_source import find_context
from app.services.prompts import ALERT_SCHEMA, label
from app.utils.log_utils import clean_log_line, aggregate_lines, line_signature
from app.utils import keyword_match
from app.services import autopilot
from app.services import second_look as second_look_service
from app.services.keyword_tuning import error_level
from app import logger
from app.utils.notification_i18n import nt

# Nombre max de lignes brutes conservées par analyse (prompt, retry, récurrence)
MAX_STORED_LINES = 100


class Orchestrator:
    """
    Orchestre le pipeline :
    1. Recevoir les nouvelles lignes
    2. Filtrer par mots-clés
    3. Analyser avec Ollama
    4. Sauvegarder en BDD
    5. Notifier si nécessaire
    """

    def __init__(self):
        self.ollama = OllamaService()
        self.notifier = NotificationService()
        self.resolution_service = None
        self.keyword_tuning = None
        self._buffers = {}  # rule_id -> {"lines": [], "task": None, "detection_id": None, "matched_keywords": set()}
        self._ollama_semaphore = asyncio.Semaphore(1)

    def ollama_load(self) -> dict:
        """Appels au modèle : en cours (un seul à la fois) et en attente."""
        semaphore = self._ollama_semaphore
        return {"running": 1 if semaphore.locked() else 0, "waiting": len(semaphore._waiters or [])}

    async def handle_new_lines(self, rule: Rule, lines: List[str]):
        """Traite les nouvelles lignes pour une règle donnée."""
        db = SessionLocal()
        try:
            db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
            if not db_rule or not db_rule.enabled or monitoring.is_paused(db):
                return

            logger.debug("Orchestrator", f"Régle '{db_rule.name}' — {len(lines)} nouvelle(s) ligne(s) reçue(s)")

            # Hook de détection de résolution (retour à la normale)
            if self.resolution_service:
                await self.resolution_service.on_new_lines(db_rule, lines)

            keywords = db_rule.get_keywords()
            excluded = db_rule.get_excluded_patterns()
            # Pilote automatique pas encore prêt : alerter quand même sur les lignes de niveau erreur explicite
            observing = autopilot.is_observing(db_rule)
            piloted = autopilot.enabled(db_rule)

            # Filtrer les lignes contenant au moins un mot-clé (les exclusions sont prioritaires)
            matching_lines = []
            matched_keywords = set()
            for line in lines:
                # 0. Skip oversized lines (defense-in-depth, BUG-01c)
                if len(line) > 10_000:
                    logger.warning("Orchestrator", f"Ligne surdimensionnée ignorée ({len(line)} chars) pour '{db_rule.name}'")
                    continue
                status, hits = keyword_match.classify(line, keywords, excluded)
                # Statistiques par motif et angles morts (lignes d'erreur qu'aucun mot-clé n'attrape)
                if self.keyword_tuning:
                    self.keyword_tuning.observe(db_rule.id, line, status, hits)
                if status == "excluded":
                    logger.debug("Orchestrator", f"Ligne exclue (pattern d'exclusion) pour '{db_rule.name}' : {line[:80]}")
                elif status == "matched":
                    matching_lines.append(line)
                    matched_keywords.update(hits)
                    logger.debug("Orchestrator", f"Match '{db_rule.name}' | kw dans : {line[:120]}")
                elif observing and error_level(line):
                    matching_lines.append(line)
                    matched_keywords.add(autopilot.OBSERVE_KEYWORD)
                elif piloted and autopilot.serious_line(line):
                    matching_lines.append(line)
                    matched_keywords.add(autopilot.SERIOUS_KEYWORD)

            if not keywords and not piloted:
                logger.debug("Orchestrator", f"Règle '{db_rule.name}' : aucun mot-clé configuré, ignorée")
                return

            if not matching_lines:
                logger.debug("Orchestrator", f"Règle '{db_rule.name}' : aucune correspondance")
                return

            rule_id = db_rule.id
            # Ajouter au buffer de cette règle
            if rule_id not in self._buffers:
                self._buffers[rule_id] = {"lines": [], "task": None, "detection_id": None, "matched_keywords": set()}

            # Générer un detection_id unique si c'est la première détection de ce cycle
            if self._buffers[rule_id]["detection_id"] is None:
                self._buffers[rule_id]["detection_id"] = uuid.uuid4().hex[:8]
                logger.debug("Orchestrator", f"Nouveau cycle de détection — ID: {self._buffers[rule_id]['detection_id']}")

            self._buffers[rule_id]["lines"].extend(matching_lines)

            self._buffers[rule_id]["matched_keywords"].update(matched_keywords)

            # Démarrer le timer anti-spam si pas déjà en cours
            # (une tâche morte, sur une base occupée par exemple, ne doit pas couper les alertes de la règle)
            task = self._buffers[rule_id]["task"]
            if task is None or task.done():
                self._buffers[rule_id]["task"] = asyncio.create_task(self._flush_buffer(rule_id))
        finally:
            db.close()

    async def _flush_buffer(self, rule_id: int):
        """Attend le délai anti-spam puis traite toutes les lignes accumulées."""
        # Phase 1 : lecture du délai (session DB courte, toujours libérée avant le sleep)
        buffer_delay = 60
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule or not rule.enabled:
                return
            buffer_delay = rule.anti_spam_delay or 60
        finally:
            db.close()  # Toujours fermée ici, avant le sleep — jamais de double-close

        await asyncio.sleep(buffer_delay)

        lines = self._buffers[rule_id]["lines"]
        detection_id = self._buffers[rule_id]["detection_id"]
        matched_keywords = list(self._buffers[rule_id]["matched_keywords"])
        self._buffers[rule_id] = {"lines": [], "task": None, "detection_id": None, "matched_keywords": set()}

        if not lines:
            return

        # Phase 2 : traitement avec une nouvelle session DB propre
        db = SessionLocal()
        try:
            # Re-fetch rule to ensure it's still enabled after sleep
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule or not rule.enabled or monitoring.is_paused(db):
                return

            config_dict = config_to_dict(db.query(GlobalConfig).first())

            # Pilote automatique : un événement répétitif déjà jugé sans gravité ne repart pas vers l'IA
            if autopilot.enabled(rule):
                lines, quiet = autopilot.quiet_lines(db, rule, lines)
                if quiet:
                    autopilot.note_quiet(rule, quiet, config_dict.get("ollama_prompt_lang", "fr"))
                    db.commit()
                    logger.debug("Orchestrator", f"'{rule.name}' : {len(quiet)} événement(s) répétitif(s) non analysé(s)")
                if not lines:
                    return
                matched_keywords = [k for k in matched_keywords if k in (autopilot.OBSERVE_KEYWORD, autopilot.SERIOUS_KEYWORD)
                                    or any(keyword_match.matching([k], l) for l in lines)]

            # Lignes brutes conservées (bornées) : servent au prompt, au retry et à la récurrence
            raw_lines = {"total": len(lines), "window_s": buffer_delay, "lines": lines[-MAX_STORED_LINES:]}
            if len(lines) > 1:
                logger.info("Orchestrator", f"Envoi groupé pour '{rule.name}' : {len(lines)} lignes")
            await self._process_match(rule, raw_lines, config_dict, db, detection_id, matched_keywords)
        except Exception as e:
            logger.error("Orchestrator", f"Erreur lors du flush buffer : {e}")
        finally:
            db.close()

    @staticmethod
    def bundle_lines(lines: List[str], total_lines: int, buffer_delay: int, max_chars: int, lang: str = 'fr') -> str:
        """Construit le bloc d'événements à partir des lignes brutes, dans l'ordre chronologique.
        Les lignes identiques à des valeurs variables près sont regroupées ([×N], dernière occurrence affichée).
        Les groupes les plus récents sont prioritaires si la limite de caractères est atteinte."""
        if len(lines) == 1:
            line = clean_log_line(lines[0])
            if len(line) > max_chars:
                logger.warning("Orchestrator", f"Ligne trop longue ({len(line)} chars), troncature à {max_chars}")
                line = line[:max_chars] + "... [TRONQUÉ]"
            return line

        groups = aggregate_lines(lines)
        header = label(lang, "group_header", total=total_lines, delay=buffer_delay, distinct=len(groups)) + "\n"
        rendered = [(f"[×{g['count']}] " if g["count"] > 1 else "") + clean_log_line(g["example"]) for g in groups]

        # Sélection du plus récent au plus ancien tant que le budget le permet
        selected = []
        current_length = len(header)
        truncated = False
        for line in reversed(rendered):
            if current_length + len(line) + 1 > max_chars:
                if not selected:
                    # Guarantee at least 1 line even if it must be truncated
                    selected.append(line[:max_chars - current_length - 20] + "…[tronqué]")
                truncated = True
                break
            selected.append(line)
            current_length += len(line) + 1

        text = header + "".join(f"\n{l}" for l in reversed(selected))
        if truncated:
            text += "\n" + label(lang, "group_truncated", max_chars=max_chars, total=total_lines)
        return text

    async def analyze_alert(self, rule: Rule, raw_lines: dict, config: dict, db,
                            matched_keywords: Optional[list] = None, detection_id: Optional[str] = None,
                            ref_time: Optional[datetime] = None, exclude_id: Optional[int] = None,
                            context: Optional[dict] = None) -> dict:
        """
        Pipeline d'analyse d'une alerte, commun aux détections automatiques, au retry, à l'analyse manuelle
        et au test de règle : bloc d'événements + contexte de la source + historique → LLM (sortie structurée).
        Retourne {"events_text", "context", "markdown", "severity", "summary", "data", "messages"}.
        """
        lang = config.get("ollama_prompt_lang", "fr")
        ref_time = ref_time or datetime.utcnow()
        events_text = self.bundle_lines(raw_lines["lines"], raw_lines["total"], raw_lines.get("window_s", 0),
                                        config.get("max_log_chars", 5000), lang)
        if context is None and rule.context_lines:
            context = await asyncio.to_thread(find_context, rule.log_file_path, raw_lines["lines"],
                                              rule.context_lines, rule.context_lines)
        signatures = {line_signature(l) for l in raw_lines["lines"]}
        history = build_history(db, rule.id, signatures, ref_time, exclude_id=exclude_id)
        notes = event_notes.for_lines(db, rule.id, raw_lines["lines"])
        messages = build_alert_messages(rule, events_text, matched_keywords, context, history, config, ref_time, notes=notes)

        logger.debug("Orchestrator", f"Envoi à Ollama — modèle={config.get('ollama_model')} | {len(messages[1]['content'])} car.")
        async with self._ollama_semaphore:
            try:
                res = await asyncio.wait_for(
                    self.ollama.chat(
                        messages,
                        url=config.get("ollama_url"),
                        model=config.get("ollama_model"),
                        think=config.get("ollama_think", True),
                        options=llm_options(config),
                        format=ALERT_SCHEMA,
                    ),
                    timeout=300.0
                )
            except asyncio.TimeoutError:
                res = {"content": "", "error": "Délai d'attente dépassé (300s)"}

        failed = bool(res.get("error") or not res.get("content"))
        if failed:
            markdown = f"[Erreur Ollama] {res.get('error') or 'Aucune réponse'}"
            parsed = {"markdown": markdown, "severity": "info", "summary": None, "data": None}
        else:
            parsed = parse_alert_response(res["content"], lang)
        context_lines = [l for key in ("before", "between", "after") for l in (context or {}).get(key) or []]
        parsed = guard_log_instruction(parsed, raw_lines["lines"] + context_lines, lang)
        # Sans réponse de l'IA, « info » n'est pas un verdict : l'alerte attend une nouvelle analyse (analysis_retry)
        parsed["failed"] = failed
        logger.debug("Orchestrator", f"Réponse Ollama reçue : {parsed['markdown'][:200]}")
        logger.add_ollama_log(messages_to_text(messages), parsed["markdown"], detection_id)
        return {"events_text": events_text, "context": context, "messages": messages, **parsed}

    async def second_look(self, rule: Rule, result: dict, config: dict, db) -> Optional[dict]:
        """Second regard (contrôles en lecture seule) sur une analyse automatique ; une panne ici ne perd pas l'alerte."""
        try:
            return await second_look_service.run(self.ollama, self._ollama_semaphore, rule, result, config, db)
        except Exception as e:
            logger.error("Orchestrator", f"Second regard impossible pour '{rule.name}' : {e}")
            return None

    @staticmethod
    def apply_result(analysis: Analysis, result: dict):
        """Reporte le résultat d'analyze_alert sur une Analysis (création ou retry)."""
        analysis.triggered_line = result["events_text"]
        analysis.ollama_response = result["markdown"]
        analysis.severity = result["severity"]
        analysis.summary = result["summary"]
        analysis.analysis_json = json.dumps(result["data"], ensure_ascii=False) if result["data"] else None
        analysis.prompt_json = json.dumps(result["messages"], ensure_ascii=False)
        ctx = result.get("context") or {}
        analysis.context_before_json = json.dumps(ctx.get("before") or [], ensure_ascii=False)
        analysis.context_after_json = json.dumps(ctx.get("after") or [], ensure_ascii=False)
        analysis.reused_from = None  # un vrai résultat de l'IA, y compris quand on relance une analyse reprise

    def reused_result(self, raw_lines: dict, prior: Analysis, config: dict) -> dict:
        """Le verdict d'une analyse précédente, pour une répétition du même événement."""
        lang = config.get("ollama_prompt_lang", "fr")
        return {
            "events_text": self.bundle_lines(raw_lines["lines"], raw_lines["total"], raw_lines.get("window_s", 0),
                                             config.get("max_log_chars", 5000), lang),
            **guard_log_instruction({
                "markdown": prior.ollama_response,
                "severity": prior.severity,
                "summary": prior.summary,
                "data": json.loads(prior.analysis_json) if prior.analysis_json else None,
            }, raw_lines["lines"], lang),
            "messages": None,
            "context": None,
        }

    async def _process_match(self, rule: Rule, raw_lines: dict, config: dict, db,
                              detection_id: str = None, matched_keywords: list = None):
        """Analyse un groupe de lignes correspondantes, sauvegarde et notifie."""
        prior = analysis_events.reusable_analysis(db, rule.id, raw_lines["lines"])
        if prior:
            logger.debug("Orchestrator", f"'{rule.name}' : répétition, verdict repris de l'analyse #{prior.id} sans appel à l'IA.")
            result = self.reused_result(raw_lines, prior, config)
        else:
            result = await self.analyze_alert(rule, raw_lines, config, db,
                                              matched_keywords=matched_keywords, detection_id=detection_id)
        # Une répétition reprend le verdict précédent sans appel à l'IA : pas de second regard non plus
        second_look = None if prior else await self.second_look(rule, result, config, db)
        severity = result["severity"]

        analysis = Analysis(
            rule_id=rule.id,
            detection_id=detection_id,
            triggered_lines_json=json.dumps(raw_lines, ensure_ascii=False),
            matched_keywords_json=json.dumps(matched_keywords or []),
            notified=False,
            analyzed_at=datetime.utcnow(),
        )
        self.apply_result(analysis, result)
        analysis.reused_from = prior.id if prior else None
        analysis.second_look_json = json.dumps(second_look, ensure_ascii=False) if second_look else None
        if result.get("failed"):
            analysis.ai_failed_at = analysis.analyzed_at
        db.add(analysis)
        db.commit()
        db.refresh(analysis)
        if result.get("failed"):
            logger.warning("Orchestrator", f"'{rule.name}' : l'IA n'a pas répondu, alerte #{analysis.id} en attente d'une nouvelle analyse.")

        # 6. Notifier — une alerte que l'IA n'a pas pu lire attend sa nouvelle analyse : notifiée à ce moment-là
        if not result.get("failed") or result.get("log_instruction"):
            await self.notify_analysis(analysis, rule, result, config, db)

        # Hook de signalement d'erreur pour la résolution
        if self.resolution_service:
            await self.resolution_service.on_error_detected(rule.id, severity=severity)

    async def notify_analysis(self, analysis: Analysis, rule: Rule, result: dict, config: dict, db, note: Optional[str] = None):
        """Notifie l'analyse si sa gravité atteint le seuil de la règle et que l'événement n'a pas déjà été signalé."""
        severity = result["severity"]
        should_notify = rule.notify_on_match
        # Une ligne qui s'adresse à l'IA est signalée quel que soit le seuil : c'est peut-être une alerte qu'on essaie de taire
        if should_notify and getattr(rule, "notify_severity_threshold", "info") and not result.get("log_instruction"):
            severity_levels = {"info": 0, "warning": 1, "critical": 2}
            sev_val = severity_levels.get(severity, 0)
            threshold_val = severity_levels.get(rule.notify_severity_threshold, 0)
            if sev_val < threshold_val:
                should_notify = False
                logger.debug("Orchestrator", f"Notification ignorée: la sévérité '{severity}' est inférieure au seuil '{rule.notify_severity_threshold}'.")

        repeats = 0
        if should_notify:
            should_notify, repeats = analysis_events.notification_decision(db, analysis)
            if not should_notify:
                logger.debug("Orchestrator", f"'{rule.name}' : événement déjà signalé, {repeats} répétition(s) sans notification.")

        if should_notify:
            try:
                await self.trigger_notification(analysis, rule, config, db, repeats=repeats, note=note)
            except Exception as e:
                logger.error("Orchestrator", f"Erreur lors de l'envoi de la notification d'alerte : {e}")

    async def reanalyze(self, analysis: Analysis, rule: Rule, config: dict, db) -> dict:
        """Nouvelle analyse d'une alerte enregistrée, à partir de ses lignes brutes et de son contexte d'origine.
        L'historique est calculé par rapport à la date de la détection. Le résultat est reporté sur l'analyse."""
        if analysis.triggered_lines_json:
            raw = json.loads(analysis.triggered_lines_json)
        else:
            lines = analysis_lines(analysis)
            raw = {"total": len(lines), "window_s": rule.anti_spam_delay or 60, "lines": lines}
            analysis.triggered_lines_json = json.dumps(raw, ensure_ascii=False)
        before, after = json.loads(analysis.context_before_json or "[]"), json.loads(analysis.context_after_json or "[]")
        context = {"before": before, "between": None, "after": after} if (before or after) else None
        result = await self.analyze_alert(
            rule, raw, config, db,
            matched_keywords=json.loads(analysis.matched_keywords_json or "[]"),
            detection_id=analysis.detection_id, ref_time=analysis.analyzed_at, exclude_id=analysis.id, context=context,
        )
        second_look = await self.second_look(rule, result, config, db)
        self.apply_result(analysis, result)
        analysis.second_look_json = json.dumps(second_look, ensure_ascii=False) if second_look else None
        analysis.ai_failed_at = (analysis.ai_failed_at or datetime.utcnow()) if result.get("failed") else None
        return result

    async def trigger_notification(self, analysis: Analysis, rule: Rule, config: dict, db, repeats: int = 0,
                                   manual: bool = False, note: Optional[str] = None) -> bool:
        """Envoie manuellement ou automatiquement une notification pour une analyse existante."""
        severity = analysis.severity
        # Mode discret : on s'arrête avant le résumé par l'IA, qui serait un appel pour rien
        if not manual and (severity or "").lower() != "critical":
            from app.services import monitoring
            if monitoring.is_quiet(db):
                logger.info("Notification", f"Mode discret : alerte {severity} de « {rule.name} » non notifiée")
                return False
        detection_id = analysis.detection_id
        line = analysis.triggered_line
        response = analysis.ollama_response
        matched_keywords = []
        if analysis.matched_keywords_json:
            matched_keywords = json.loads(analysis.matched_keywords_json)
            
        lang = config.get("site_lang", config.get("ollama_prompt_lang", "fr"))
        logger.debug("Notification", f"Notification lang={lang} (site_lang={config.get('site_lang')}, ollama_prompt_lang={config.get('ollama_prompt_lang')})")
        det_id_label = f" [ID: {detection_id}]" if detection_id else ""
        logger.debug("Notification", f"Envoi notification via '{config.get('notification_method')}' pour règle '{rule.name}'")
        instance_prefix = f"[{config.get('instance_name')}] " if config.get('instance_name') else ""
        subject = f"{instance_prefix}[Sentinel] {nt('alert', lang)} {severity.upper()} : {rule.name}{det_id_label}"
        
        severity_emoji = "🔴" if severity == "critical" else "🟠" if severity == "warning" else "🔵"
        
        body = f"""
        <h2>{severity_emoji} {nt('alert_title', lang)}</h2>
        <p><strong>{nt('rule', lang)}:</strong> {html.escape(rule.name or '')}</p>
        <p><strong>{nt('detection_id', lang)}:</strong> <code>{detection_id or 'N/A'}</code></p>
        <p><strong>{nt('keywords', lang)}:</strong> {html.escape(', '.join(matched_keywords)) if matched_keywords else 'N/A'}</p>
        <hr/>
        <p><strong>{nt('triggered_line', lang)}:</strong></p>
        <pre><code>{html.escape(line or '')}</code></pre>
        <p><strong>{nt('ollama_analysis', lang)}:</strong></p>
        <blockquote>{html.escape(response or '')}</blockquote>
        <p><strong>{nt('severity', lang)}:</strong> {severity.upper()}</p>
        """
        
        # Si Apprise ou Discord, on prépare une version plus lisible pour Discord/Telegram (souvent Markdown)
        if config.get("notification_method") in ("apprise", "discord"):
            body = f"""### {severity_emoji} {nt('alert', lang)} Sentinel : {rule.name}
**ID:** `{detection_id or 'N/A'}` | **{nt('severity', lang)}:** {severity.upper()}
**{nt('keywords', lang)}:** {', '.join(matched_keywords) if matched_keywords else 'N/A'}

**{nt('triggered_line', lang)}:**
`{line}`

**{nt('ollama_analysis', lang)}:**
{response}
"""

        # Gestion du résumé IA si nécessaire (pour Apprise/Discord/etc.)
        max_chars = config.get("apprise_max_chars", 1900)
        notify_body = body
        lang = config.get("ollama_prompt_lang", "fr")

        is_short_channel = config.get("notification_method") in ("apprise", "discord")
        if is_short_channel and len(body) > max_chars and analysis.summary:
            # Résumé structuré déjà produit par l'analyse : pas de second appel LLM
            notify_body = f"""### {severity_emoji} {nt('alert', lang)} Sentinel ({nt('analysis_summary', lang)}) : {rule.name}
**ID:** `{detection_id or 'N/A'}` | **{nt('severity', lang)}:** {severity.upper()}

**{nt('analysis_summary', lang)} :**
{analysis.summary}
"""
            actions = (json.loads(analysis.analysis_json).get("actions") or []) if analysis.analysis_json else []
            footer = f"\n{nt('full_analysis_available', lang)}"
            if actions:
                actions_block = f"\n**{label(lang, 'actions')} :**\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1)) + "\n"
                if len(notify_body) + len(actions_block) + len(footer) <= max_chars:
                    notify_body += actions_block
            notify_body += footer
        elif is_short_channel and len(body) > max_chars:
            logger.debug("Notification", f"Analyse trop longue ({len(body)} chars), demande de résumé simplifié à Ollama...")
            summary_prompt = nt('summary_prompt', lang).format(max_chars=max_chars - 500, response=response)
            async with self._ollama_semaphore:
                try:
                    summary = await asyncio.wait_for(
                        self.ollama.analyze_async(
                            prompt=summary_prompt,
                            url=config.get("ollama_url"),
                            model=config.get("ollama_model"),
                            think=False, # Pas de raisonnement pour un résumé court
                            options={
                                "temperature": 0.1, # Résumé toujours à basse température
                                # Même num_ctx que l'analyse : un num_ctx différent force Ollama à recharger le modèle,
                                # et 2048 tronquait l'analyse en entrée
                                "num_ctx": config.get("ollama_ctx", 4096),
                            }
                        ),
                        timeout=60.0
                    )
                except asyncio.TimeoutError:
                    summary = "[Erreur Ollama] Délai d'attente dépassé pour le résumé (60s)"
            if not (isinstance(summary, str) and summary.startswith("[Erreur Ollama]")):
                logger.add_ollama_log(summary_prompt, summary, detection_id)
                notify_body = f"""### {severity_emoji} {nt('alert', lang)} Sentinel ({nt('analysis_summary', lang)}) : {rule.name}
**ID:** `{detection_id or 'N/A'}` | **{nt('severity', lang)}:** {severity.upper()}

**{nt('analysis_summary', lang)} :**
{summary}

{nt('full_analysis_available', lang)}"""

        notes = ([nt('repeat_note', lang).format(n=repeats)] if repeats else []) + ([note] if note else [])
        for text in notes:
            notify_body += f"\n\n{text}" if is_short_channel else f"<p><em>{html.escape(text)}</em></p>"

        sent = await asyncio.to_thread(self.notifier.send, subject, notify_body, config,
                                       kind="manual" if manual else "alert", severity=severity)
        analysis.notified = bool(sent)
        db.commit()
        return True

    # La méthode _clean_log_line a été déplacée dans app.utils.log_utils.clean_log_line

    def _detect_severity(self, response: str) -> str:
        """Détermine la sévérité à partir d'une réponse texte libre (voir alert_analysis.detect_severity)."""
        return detect_severity(response)
