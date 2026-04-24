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
        buffer_delay = 10  # secondes d'anti-spam (peut devenir paramétrable en DB)
        await asyncio.sleep(buffer_delay)

        lines = self._buffers[rule_id]["lines"]
        self._buffers[rule_id] = {"lines": [], "task": None}

        if not lines:
            return

        db = SessionLocal()
        try:
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
                "debug_mode": config.debug_mode if config else False,
            }

            if len(lines) == 1:
                await self._process_match(rule, lines[0], config_dict, db)
            else:
                # Regrouper les lignes
                total_lines = len(lines)
                # On limite à 30 lignes max dans le prompt pour ne pas exploser le contexte
                recent_lines = lines[-30:]
                bundled_text = f"Ces {total_lines} événements correspondants sont apparus dans les {buffer_delay} dernières secondes. Voici un extrait des plus récents :\n"
                bundled_text += "\n".join(recent_lines)
                
                logger.info("Orchestrator", f"Envoi groupé pour '{rule.name}' : {total_lines} lignes")
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

        # 3. Appeler Ollama (dans un thread séparé pour ne pas bloquer l'Event Loop)
        logger.debug("Orchestrator", f"Envoi à Ollama — modèle={config.get('ollama_model')} | ligne={line[:80]}")
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
        if rule.notify_on_match:
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

    def _build_prompt(self, rule: Rule, line: str, system_prompt: str, context_lines: list = None) -> str:
        """Construit le prompt pour Ollama."""
        context_block = ""
        if context_lines:
            context_block = "\n        Lignes de contexte précédentes:\n" + "\n".join(f"        {l}" for l in context_lines) + "\n"

        base_prompt = f"""
        Analyse la ligne de log suivante et détermine sa sévérité (info, warning, critical).
        Fournis un résumé court de l'incident.

        Contexte de l'application: {rule.application_context}
{context_block}        Ligne déclenchante: {line}
        """

        if system_prompt:
            return f"{system_prompt}\n\n{base_prompt}"
        return base_prompt

    def _detect_severity(self, response: str) -> str:
        """Détermine la sévérité à partir de la réponse Ollama."""
        lower_resp = response.lower()
        if "critical" in lower_resp or "urgent" in lower_resp:
            return "critical"
        elif "warning" in lower_resp or "warn" in lower_resp:
            return "warning"
        return "info"
