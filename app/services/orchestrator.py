import asyncio
from typing import List, Optional
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services.ollama_service import OllamaService
from app.services.notification_service import NotificationService


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

    async def handle_new_lines(self, rule: Rule, lines: List[str]):
        """Traite les nouvelles lignes pour une règle donnée."""
        if not rule.enabled:
            return

        keywords = rule.get_keywords()
        if not keywords:
            return

        # Filtrer les lignes contenant au moins un mot-clé
        matching_lines = []
        for line in lines:
            if any(kw.lower() in line.lower() for kw in keywords):
                matching_lines.append(line)

        if not matching_lines:
            return

        # Récupérer la config globale
        db = SessionLocal()
        try:
            config = db.query(GlobalConfig).first()
            config_dict = {
                "smtp_host": config.smtp_host if config else "",
                "smtp_port": config.smtp_port if config else 587,
                "smtp_user": config.smtp_user if config else "",
                "smtp_password": config.smtp_password if config else "",
                "smtp_tls": config.smtp_tls if config else True,
                "ollama_url": config.ollama_url if config else "http://host.docker.internal:11434",
                "ollama_model": config.ollama_model if config else "llama3",
                "system_prompt": config.system_prompt if config else "",
                "notification_method": config.notification_method if config else "smtp",
                "apprise_url": config.apprise_url if config else "",
            }

            for line in matching_lines:
                await self._process_match(rule, line, config_dict, db)
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
        response = await asyncio.to_thread(
            self.ollama.analyze,
            prompt=prompt,
            url=config.get("ollama_url"),
            model=config.get("ollama_model"),
        )

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
            subject = f"[Sentinel] Alerte {severity.upper()} : {rule.name}"
            body = f"""
            <h2>Alerte Log Sentinel</h2>
            <p><strong>Règle:</strong> {rule.name}</p>
            <p><strong>Ligne déclenchante:</strong> <code>{line}</code></p>
            <p><strong>Analyse Ollama:</strong></p>
            <blockquote>{response}</blockquote>
            <p><strong>Sévérité:</strong> {severity}</p>
            """
            await asyncio.to_thread(self.notifier.send, subject, body, config)
            analysis.notified = True
            db.commit()

    def _build_prompt(self, rule: Rule, line: str, system_prompt: str) -> str:
        """Construit le prompt pour Ollama."""
        base_prompt = f"""
        Analyse la ligne de log suivante et détermine sa sévérité (info, warning, critical).
        Fournis un résumé court de l'incident.

        Contexte de l'application: {rule.application_context}
        Ligne: {line}
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
