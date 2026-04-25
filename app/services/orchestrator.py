import asyncio
from typing import List, Optional
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services.ollama_service import OllamaService
from app.services.notification_service import NotificationService
from app import logger


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
        self._buffers = {}  # rule_id -> {"lines": [], "task": None}
        self._ollama_semaphore = asyncio.Semaphore(1)

    async def handle_new_lines(self, rule: Rule, lines: List[str]):
        """Traite les nouvelles lignes pour une règle donnée."""
        if not rule.enabled:
            return

        logger.debug("Orchestrator", f"Régle '{rule.name}' — {len(lines)} nouvelle(s) ligne(s) reçue(s)")

        keywords = rule.get_keywords()
        if not keywords:
            logger.debug("Orchestrator", f"Règle '{rule.name}' : aucun mot-clé configuré, ignorée")
            return

        # Filtrer les lignes contenant au moins un mot-clé
        matching_lines = []
        for line in lines:
            if any(kw.lower() in line.lower() for kw in keywords):
                matching_lines.append(line)
                logger.debug("Orchestrator", f"Match '{rule.name}' | kw dans : {line[:120]}")

        if not matching_lines:
            logger.debug("Orchestrator", f"Règle '{rule.name}' : aucune correspondance")
            return

        # Ajouter au buffer de cette règle
        if rule.id not in self._buffers:
            self._buffers[rule.id] = {"lines": [], "task": None}
            
        self._buffers[rule.id]["lines"].extend(matching_lines)

        # Démarrer le timer anti-spam si pas déjà en cours
        if self._buffers[rule.id]["task"] is None:
            self._buffers[rule.id]["task"] = asyncio.create_task(self._flush_buffer(rule.id))

    async def _flush_buffer(self, rule_id: int):
        """Attend le délai anti-spam puis traite toutes les lignes accumulées."""
        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule or not rule.enabled:
                return

            buffer_delay = rule.anti_spam_delay or 60
            db.close() # Free the connection during the sleep
            
            await asyncio.sleep(buffer_delay)

            lines = self._buffers[rule_id]["lines"]
            self._buffers[rule_id] = {"lines": [], "task": None}

            if not lines:
                return
                
            db = SessionLocal()
            # Re-fetch rule to ensure it's still enabled after sleep
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule or not rule.enabled:
                return

            config = db.query(GlobalConfig).first()
            config_dict = {
                "smtp_host": config.smtp_host if config else "",
                "smtp_port": config.smtp_port if config else 587,
                "smtp_user": config.smtp_user if config else "",
                "smtp_password": config.smtp_password if config else "",
                "smtp_recipient": config.smtp_recipient if config else "",
                "smtp_tls": config.smtp_tls if config else True,
                "smtp_ssl_mode": config.smtp_ssl_mode if config else "starttls",
                "ollama_url": config.ollama_url if config else "http://host.docker.internal:11434",
                "ollama_model": config.ollama_model if config else "llama3",
                "system_prompt": config.system_prompt if config else "",
                "notification_method": config.notification_method if config else "smtp",
                "apprise_url": config.apprise_url if config else "",
                "apprise_tags": config.apprise_tags if config else "",
                "apprise_max_chars": config.apprise_max_chars if config else 1900,
                "max_log_chars": config.max_log_chars if config else 5000,
                "debug_mode": config.debug_mode if config else False,
            }

            max_chars = config_dict.get("max_log_chars", 5000)

            if len(lines) == 1:
                line = self._clean_log_line(lines[0])
                # Si la ligne est trop longue, on la tronque
                if len(line) > max_chars:
                    logger.warning("Orchestrator", f"Ligne trop longue ({len(line)} chars), troncature à {max_chars}")
                    line = line[:max_chars] + "... [TRONQUÉ]"
                
                await self._process_match(rule, line, config_dict, db)
            else:
                # Regrouper les lignes
                total_lines = len(lines)
                # On limite à 30 lignes max dans le prompt pour ne pas exploser le contexte
                recent_lines = [self._clean_log_line(l) for l in lines[-30:]]
                
                bundled_text = f"Ces {total_lines} événements correspondants sont apparus dans les {buffer_delay} dernières secondes. Voici un extrait des plus récents :\n"
                
                current_length = len(bundled_text)
                for line in reversed(recent_lines):
                    line_to_add = f"\n{line}"
                    if current_length + len(line_to_add) > max_chars:
                        bundled_text += "\n... [Certaines lignes ont été omises car trop longues]"
                        break
                    bundled_text += line_to_add
                    current_length += len(line_to_add)
                
                logger.info("Orchestrator", f"Envoi groupé pour '{rule.name}' : {total_lines} lignes ({len(bundled_text)} chars)")
                await self._process_match(rule, bundled_text, config_dict, db)
        except Exception as e:
            logger.error("Orchestrator", f"Erreur lors du flush buffer : {e}")
        finally:
            db.close()

    async def _process_match(self, rule: Rule, line: str, config: dict, db):
        """Traite une ligne correspondante."""
        # 1. Préparer le contexte
        context_before = []
        context_after = []
        # Note: Dans une implémentation complète, on récupérerait le contexte depuis le fichier.
        # Ici, on simule ou on utilise les lignes disponibles si possible.
        # Pour simplifier, on passe le contexte vide ou on le récupère si disponible.

        # 2. Construire le prompt
        prompt = self._build_prompt(rule, line, config.get("system_prompt", ""))

        # 3. Appeler Ollama (sous verrou pour éviter de surcharger le CPU)
        logger.debug("Orchestrator", f"Envoi à Ollama — modèle={config.get('ollama_model')} | ligne={line[:80]}")
        async with self._ollama_semaphore:
            response = await asyncio.to_thread(
                self.ollama.analyze,
                prompt=prompt,
                url=config.get("ollama_url"),
                model=config.get("ollama_model"),
            )
        logger.debug("Orchestrator", f"Réponse Ollama reçue : {response[:200]}")

        # 4. Déterminer la sévérité (simple heuristic ou parsing de la réponse)
        severity = self._detect_severity(response)

        # 5. Sauvegarder en BDD
        analysis = Analysis(
            rule_id=rule.id,
            triggered_line=line,
            context_before_json="[]",  # À implémenter si contexte disponible
            context_after_json="[]",
            ollama_response=response,
            severity=severity,
            notified=False,
            analyzed_at=datetime.utcnow(),
        )
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
            logger.debug("Orchestrator", f"Envoi notification via '{config.get('notification_method')}' pour règle '{rule.name}'")
            subject = f"[Sentinel] Alerte {severity.upper()} : {rule.name}"
            
            body = f"""
            <h2>Alerte Log to LLM Sentinel</h2>
            <p><strong>Règle:</strong> {rule.name}</p>
            <p><strong>Ligne déclenchante:</strong> <code>{line}</code></p>
            <p><strong>Analyse Ollama:</strong></p>
            <blockquote>{response}</blockquote>
            <p><strong>Sévérité:</strong> {severity}</p>
            """

            # Gestion du résumé IA si nécessaire (pour Apprise/Discord/etc.)
            max_chars = config.get("apprise_max_chars", 1900)
            notify_body = body

            if config.get("notification_method") == "apprise" and len(body) > max_chars:
                logger.debug("Orchestrator", f"Analyse trop longue ({len(body)} chars), demande de résumé simplifié à Ollama...")
                summary_prompt = f"Résume l'analyse suivante en moins de {max_chars - 400} caractères. Garde l'essentiel (Sévérité, Cause, Action). Format clair.\n\nAnalyse : {response}"
                async with self._ollama_semaphore:
                    summary = await asyncio.to_thread(
                        self.ollama.analyze,
                        prompt=summary_prompt,
                        url=config.get("ollama_url"),
                        model=config.get("ollama_model"),
                    )
                if not (isinstance(summary, str) and summary.startswith("[Erreur Ollama]")):
                    notify_body = f"""
                    <h2>Alerte Log to LLM Sentinel (Résumé)</h2>
                    <p><strong>Règle:</strong> {rule.name}</p>
                    <p><strong>Résumé:</strong></p>
                    <blockquote>{summary}</blockquote>
                    <p><strong>Sévérité:</strong> {severity}</p>
                    <p><em>(Analyse complète disponible dans l'interface)</em></p>
                    """

            await asyncio.to_thread(self.notifier.send, subject, notify_body, config)
            analysis.notified = True
            db.commit()

    def _clean_log_line(self, line: str) -> str:
        """Tente de nettoyer une ligne de log si elle est au format JSON (ex: Nextcloud)."""
        import json
        stripped = line.strip()
        if not (stripped.startswith('{') and stripped.endswith('}')):
            return line

        try:
            data = json.loads(stripped)
            # Pour Nextcloud : extraire message, app, et éventuellement exception
            msg = data.get("message", "")
            app = data.get("app", "")
            exc = data.get("exception", "") or data.get("data", {}).get("exception", "")
            
            if msg:
                cleaned = f"[{app}] {msg}"
                if exc:
                    # On ne garde que le début de l'exception si elle est énorme
                    exc_str = str(exc)
                    if len(exc_str) > 1000:
                        exc_str = exc_str[:1000] + "... [EXCEPTION TRONQUÉE]"
                    cleaned += f" | Exception: {exc_str}"
                return cleaned
            return line # Fallback si pas de champ message
        except:
            return line

    def _build_prompt(self, rule: Rule, line: str, system_prompt: str, context_lines: list = None) -> str:
        """Construit le prompt pour Ollama."""
        context_block = ""
        if context_lines:
            context_block = "\n        Lignes de contexte précédentes:\n" + "\n".join(f"        {l}" for l in context_lines) + "\n"

        base_prompt = f"""
        Analyse la ligne de log suivante et détermine sa sévérité.
        Ta réponse DOIT impérativement commencer par une ligne indiquant la sévérité sous ce format EXACT :
        SEVERITY: [info|warning|critical]

        Ensuite, fournis un résumé court et explicatif de l'incident.

        Contexte de l'application: {rule.application_context}
{context_block}        Ligne déclenchante: {line}
        """

        if system_prompt:
            return f"{system_prompt}\n\n{base_prompt}"
        return base_prompt

    def _detect_severity(self, response: str) -> str:
        """Détermine la sévérité à partir de la réponse Ollama."""
        # Priorité : chercher le tag explicite demandé dans le prompt
        lines = response.splitlines()
        for line in lines[:10]: # On regarde les premières lignes
            l = line.upper()
            if "SEVERITY:" in l:
                if "CRITICAL" in l: return "critical"
                if "WARNING" in l: return "warning"
                if "INFO" in l: return "info"

        # Fallback : recherche heuristique plus stricte
        lower_resp = response.lower()
        # On cherche des mentions isolées ou en début de phrase pour éviter les faux positifs
        # comme "ce n'est pas une erreur CRITICAL"
        if "severity: critical" in lower_resp or "sévérité: critical" in lower_resp:
            return "critical"
        if "severity: warning" in lower_resp or "sévérité: warning" in lower_resp:
            return "warning"
        if "severity: info" in lower_resp or "sévérité: info" in lower_resp:
            return "info"
            
        # Ultime fallback (moins fiable)
        if "critical" in lower_resp or "urgent" in lower_resp:
            return "critical"
        elif "warning" in lower_resp or "warn" in lower_resp:
            return "warning"
        return "info"
