import json
from datetime import datetime, timedelta
import asyncio
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, MetaAnalysisConfig, MetaAnalysisResult
from app.services.ollama_service import OllamaService
from app.services.notification_service import NotificationService
from app import logger


class MetaAnalysisService:
    """
    Service gérant les méta-analyses périodiques.
    """

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator

    async def run_scheduled_analyses(self):
        """
        Vérifie toutes les configurations actives et lance celles qui ont dépassé leur intervalle.
        """
        db = SessionLocal()
        try:
            configs = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.enabled == True).all()
            now = datetime.utcnow()

            for config in configs:
                # Vérifier si l'intervalle est écoulé
                if config.last_run_at:
                    next_run = config.last_run_at + timedelta(hours=config.interval_hours)
                    if now < next_run:
                        continue  # Pas encore le moment

                logger.info("MetaAnalysisService", f"Déclenchement de la méta-analyse '{config.name}' (ID: {config.id})")
                
                # Exécution sans bloquer la boucle principale
                await self.execute_meta_analysis(config.id, now)

        except Exception as e:
            logger.error("MetaAnalysisService", f"Erreur dans le planificateur de méta-analyses : {str(e)}")
        finally:
            db.close()

    async def execute_meta_analysis(self, config_id: int, trigger_time: datetime = None):
        """
        Exécute la méta-analyse : récupération, compression, appel LLM, sauvegarde et notification.
        """
        if trigger_time is None:
            trigger_time = datetime.utcnow()

        db = SessionLocal()
        try:
            config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
            if not config:
                return {"status": "error", "message": "Config introuvable"}

            global_cfg = db.query(GlobalConfig).first()
            if not global_cfg:
                return {"status": "error", "message": "Config globale introuvable"}

            # Parser les règles ciblées
            rule_ids = []
            if config.rule_ids_json:
                try:
                    rule_ids = json.loads(config.rule_ids_json)
                except Exception:
                    rule_ids = []

            # Définir la période d'analyse
            period_start = config.last_run_at if config.last_run_at else (trigger_time - timedelta(hours=config.interval_hours))
            period_end = trigger_time

            # Récupérer les analyses
            query = db.query(Analysis, Rule).outerjoin(Rule, Analysis.rule_id == Rule.id).filter(
                Analysis.analyzed_at >= period_start,
                Analysis.analyzed_at <= period_end
            )

            if rule_ids:
                query = query.filter(Analysis.rule_id.in_(rule_ids))

            results = query.order_by(desc(Analysis.analyzed_at)).limit(config.max_analyses).all()

            if not results:
                logger.info("MetaAnalysisService", f"Aucune analyse à traiter pour '{config.name}' dans la période.")
                # Mettre à jour quand même pour ne pas relancer en boucle
                config.last_run_at = trigger_time
                db.commit()
                return {"status": "skipped", "message": "Aucune donnée"}

            analyses_count = len(results)
            logger.info("MetaAnalysisService", f"Méta-analyse '{config.name}' : {analyses_count} événements trouvés.")

            # Compresser les données pour le prompt
            # On trie par date croissante pour la lecture (le LLM préfère la chronologie)
            results.reverse()
            
            compressed_data = []
            detection_ids = []
            all_matched_keywords = set()
            
            for analysis, rule in results:
                if analysis.detection_id:
                    detection_ids.append(analysis.detection_id)
                if analysis.matched_keywords_json:
                    try:
                        kws = json.loads(analysis.matched_keywords_json)
                        all_matched_keywords.update(kws)
                    except Exception:
                        pass
                rule_name = rule.name if rule else "Inconnue"
                date_str = analysis.analyzed_at.strftime("%Y-%m-%d %H:%M:%S")
                # Tronquer la réponse IA pour ne garder que le premier paragraphe ou les 200 premiers caractères
                short_ia = analysis.ollama_response.split("\n\n")[0][:200] + "..." if analysis.ollama_response else "N/A"
                
                block = (
                    f"[{date_str}] [SEVERITY: {analysis.severity.upper()}] [Règle: {rule_name}] [ID: {analysis.detection_id}]\n"
                    f"Ligne: {analysis.triggered_line[:500]}\n"
                    f"IA unitaire: {short_ia}"
                )
                compressed_data.append(block)

            events_text = "\n\n".join(compressed_data)

            # Construire le prompt
            prompt = (
                f"{config.system_prompt}\n\n"
                f"Voici les {analyses_count} événements (limité aux plus récents) survenus entre {period_start.strftime('%Y-%m-%d %H:%M')} et {period_end.strftime('%Y-%m-%d %H:%M')}:\n"
                f"----------------------------------------\n"
                f"{events_text}\n"
                f"----------------------------------------\n"
                "Réalise une synthèse experte de cette situation globale, croise les informations si plusieurs services sont touchés, et propose des recommandations générales."
            )

            # Appel à Ollama via l'orchestrateur
            ollama = self.orchestrator.ollama if self.orchestrator else OllamaService()
            ollama_url = global_cfg.ollama_url or "http://ollama:11434"
            ollama_model = global_cfg.ollama_model or "gemma4:e4b"
            
            logger.debug("MetaAnalysisService", f"Envoi prompt méta-analyse (Taille: {len(prompt)} car., Contexte: {config.context_size})")

            if self.orchestrator:
                async with self.orchestrator._ollama_semaphore:
                    response_text = await ollama.analyze_async(
                        prompt=prompt,
                        url=ollama_url,
                        model=ollama_model,
                        options={"temperature": 0.2, "num_ctx": config.context_size},
                        think=True
                    )
            else:
                response_text = await ollama.analyze_async(
                    prompt=prompt,
                    url=ollama_url,
                    model=ollama_model,
                    options={"temperature": 0.2, "num_ctx": config.context_size},
                    think=True
                )

            # Sauvegarder le résultat
            meta_result = MetaAnalysisResult(
                config_id=config.id,
                period_start=period_start,
                period_end=period_end,
                analyses_count=analyses_count,
                detection_ids_json=json.dumps(detection_ids),
                matched_keywords_json=json.dumps(list(all_matched_keywords)),
                ollama_response=response_text
            )
            db.add(meta_result)
            
            config.last_run_at = trigger_time
            db.commit()

            # Notification
            if config.notify_enabled:
                await self._send_notification(meta_result, config, global_cfg)

            return {"status": "ok", "analyses_count": analyses_count}

        except Exception as e:
            logger.error("MetaAnalysisService", f"Erreur lors de l'exécution de la méta-analyse {config_id} : {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    async def _send_notification(self, result: MetaAnalysisResult, config: MetaAnalysisConfig, global_cfg: GlobalConfig):
        """
        Envoie une notification globale pour la méta-analyse.
        """
        notifier = NotificationService()
        
        subject = f"[Sentinel] Méta-Analyse : {config.name}"
        
        body_html = f"""
        <h2>📊 Méta-Analyse Sentinel : {config.name}</h2>
        <p><strong>Période:</strong> du {result.period_start.strftime('%Y-%m-%d %H:%M')} au {result.period_end.strftime('%Y-%m-%d %H:%M')}</p>
        <p><strong>Événements analysés:</strong> {result.analyses_count}</p>
        <hr/>
        <h3>Synthèse IA :</h3>
        <blockquote>{result.ollama_response}</blockquote>
        """
        
        notify_body = body_html

        if global_cfg.notification_method == "apprise":
            notify_body = f"""### 📊 Méta-Analyse : {config.name}
**Période:** {result.period_start.strftime('%Y-%m-%d %H:%M')} - {result.period_end.strftime('%Y-%m-%d %H:%M')}
**Événements:** {result.analyses_count}

**Synthèse:**
{result.ollama_response}
"""
            max_chars = global_cfg.apprise_max_chars or 1900
            if len(notify_body) > max_chars:
                notify_body = notify_body[:max_chars-100] + "\n\n... [TRONQUÉ]"

        # Send via thread to not block asyncio
        await asyncio.to_thread(notifier.send, subject, notify_body, {
            "smtp_host": global_cfg.smtp_host,
            "smtp_port": global_cfg.smtp_port,
            "smtp_user": global_cfg.smtp_user,
            "smtp_password": global_cfg.smtp_password,
            "smtp_recipient": global_cfg.smtp_recipient,
            "smtp_tls": global_cfg.smtp_tls,
            "smtp_ssl_mode": global_cfg.smtp_ssl_mode,
            "notification_method": global_cfg.notification_method,
            "apprise_url": global_cfg.apprise_url,
            "apprise_tags": global_cfg.apprise_tags,
        })
