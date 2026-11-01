import asyncio
import uuid
import json
from typing import List, Optional
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services.ollama_service import OllamaService
from app.services.notification_service import NotificationService
from app.services.app_config import config_to_dict, llm_options
from app.services.alert_analysis import (build_history, build_alert_messages, messages_to_text,
                                         parse_alert_response, detect_severity)
from app.services.log_source import find_context
from app.services.prompts import ALERT_SCHEMA, label
from app.utils.log_utils import clean_log_line, aggregate_lines, line_signature
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
        self._buffers = {}  # rule_id -> {"lines": [], "task": None, "detection_id": None, "matched_keywords": set()}
        self._ollama_semaphore = asyncio.Semaphore(1)

    async def handle_new_lines(self, rule: Rule, lines: List[str]):
        """Traite les nouvelles lignes pour une règle donnée."""
        db = SessionLocal()
        try:
            db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
            if not db_rule or not db_rule.enabled:
                return

            logger.debug("Orchestrator", f"Régle '{db_rule.name}' — {len(lines)} nouvelle(s) ligne(s) reçue(s)")

            # Hook de détection de résolution (retour à la normale)
            if self.resolution_service:
                await self.resolution_service.on_new_lines(db_rule, lines)

            keywords = db_rule.get_keywords()
            if not keywords:
                logger.debug("Orchestrator", f"Règle '{db_rule.name}' : aucun mot-clé configuré, ignorée")
                return

            excluded = db_rule.get_excluded_patterns()

            # Filtrer les lignes contenant au moins un mot-clé
            matching_lines = []
            for line in lines:
                # 0. Skip oversized lines (defense-in-depth, BUG-01c)
                if len(line) > 10_000:
                    logger.warning("Orchestrator", f"Ligne surdimensionnée ignorée ({len(line)} chars) pour '{db_rule.name}'")
                    continue
                # 1. Exclusion patterns (negative keywords) — priorité max
                if excluded and any(pat.lower() in line.lower() for pat in excluded):
                    logger.debug("Orchestrator", f"Ligne exclue (pattern d'exclusion) pour '{db_rule.name}' : {line[:80]}")
                    continue
                # 2. Positive keyword match
                if any(kw.lower() in line.lower() for kw in keywords):
                    matching_lines.append(line)
                    logger.debug("Orchestrator", f"Match '{db_rule.name}' | kw dans : {line[:120]}")

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

            # Collecter les mots-clés matchés
            for line in matching_lines:
                for kw in keywords:
                    if kw.lower() in line.lower():
                        self._buffers[rule_id]["matched_keywords"].add(kw)

            # Démarrer le timer anti-spam si pas déjà en cours
            if self._buffers[rule_id]["task"] is None:
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
            if not rule or not rule.enabled:
                return

            config_dict = config_to_dict(db.query(GlobalConfig).first())

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
        messages = build_alert_messages(rule, events_text, matched_keywords, context, history, config, ref_time)

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

        if res.get("error") or not res.get("content"):
            markdown = f"[Erreur Ollama] {res.get('error') or 'Aucune réponse'}"
            parsed = {"markdown": markdown, "severity": "info", "summary": None, "data": None}
        else:
            parsed = parse_alert_response(res["content"], lang)
        logger.debug("Orchestrator", f"Réponse Ollama reçue : {parsed['markdown'][:200]}")
        logger.add_ollama_log(messages_to_text(messages), parsed["markdown"], detection_id)
        return {"events_text": events_text, "context": context, "messages": messages, **parsed}

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

    async def _process_match(self, rule: Rule, raw_lines: dict, config: dict, db,
                              detection_id: str = None, matched_keywords: list = None):
        """Analyse un groupe de lignes correspondantes, sauvegarde et notifie."""
        result = await self.analyze_alert(rule, raw_lines, config, db,
                                          matched_keywords=matched_keywords, detection_id=detection_id)
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
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        # 6. Notifier
        should_notify = rule.notify_on_match
        if should_notify and getattr(rule, "notify_severity_threshold", "info"):
            severity_levels = {"info": 0, "warning": 1, "critical": 2}
            sev_val = severity_levels.get(severity, 0)
            threshold_val = severity_levels.get(rule.notify_severity_threshold, 0)
            if sev_val < threshold_val:
                should_notify = False
                logger.debug("Orchestrator", f"Notification ignorée: la sévérité '{severity}' est inférieure au seuil '{rule.notify_severity_threshold}'.")

        if should_notify:
            try:
                await self.trigger_notification(analysis, rule, config, db)
            except Exception as e:
                logger.error("Orchestrator", f"Erreur lors de l'envoi de la notification d'alerte : {e}")

        # Hook de signalement d'erreur pour la résolution
        if self.resolution_service:
            await self.resolution_service.on_error_detected(rule.id, severity=severity)
            
    async def trigger_notification(self, analysis: Analysis, rule: Rule, config: dict, db) -> bool:
        """Envoie manuellement ou automatiquement une notification pour une analyse existante."""
        severity = analysis.severity
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
        <p><strong>{nt('rule', lang)}:</strong> {rule.name}</p>
        <p><strong>{nt('detection_id', lang)}:</strong> <code>{detection_id or 'N/A'}</code></p>
        <p><strong>{nt('keywords', lang)}:</strong> {', '.join(matched_keywords) if matched_keywords else 'N/A'}</p>
        <hr/>
        <p><strong>{nt('triggered_line', lang)}:</strong></p>
        <pre><code>{line}</code></pre>
        <p><strong>{nt('ollama_analysis', lang)}:</strong></p>
        <blockquote>{response}</blockquote>
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

        await asyncio.to_thread(self.notifier.send, subject, notify_body, config)
        analysis.notified = True
        db.commit()
        return True

    # La méthode _clean_log_line a été déplacée dans app.utils.log_utils.clean_log_line

    def _detect_severity(self, response: str) -> str:
        """Détermine la sévérité à partir d'une réponse texte libre (voir alert_analysis.detect_severity)."""
        return detect_severity(response)
