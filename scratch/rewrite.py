import sys
import json
import re

content = open('app/services/meta_service.py', 'r', encoding='utf-8').read()

# 1. Replace run_scheduled_analyses
new_run = '''
    def _should_run_schedule(self, config, now) -> bool:
        if not config.schedule_time: return False
        try:
            h, m = map(int, config.schedule_time.split(':'))
        except:
            return False
            
        if config.last_run_at and config.last_run_at.date() == now.date():
            return False
            
        if now.hour < h or (now.hour == h and now.minute < m):
            return False
            
        if config.schedule_type == 'daily':
            return True
        elif config.schedule_type == 'weekly':
            return now.weekday() == (config.schedule_day - 1)
        elif config.schedule_type == 'monthly':
            return now.day == config.schedule_day
            
        return False

    async def run_scheduled_analyses(self):
        db = SessionLocal()
        try:
            configs = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.enabled == True).all()
            now = datetime.utcnow()

            for config in configs:
                if self._should_run_schedule(config, now):
                    logger.info('MetaAnalysisService', f'Déclenchement calendaire de la méta-analyse {config.name}')
                    await self.execute_meta_analysis(config.id, now)

        except Exception as e:
            logger.error('MetaAnalysisService', f'Erreur planificateur: {str(e)}')
        finally:
            db.close()
'''

content = re.sub(r'    async def run_scheduled_analyses\(self\):.*?        finally:\n            db\.close\(\)\n', new_run, content, flags=re.DOTALL)

# 2. Add get_pending_context
new_get_pending = '''
    async def get_pending_context(self, config_id: int):
        db = SessionLocal()
        try:
            config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
            if not config: return {'status': 'error', 'message': 'Config introuvable'}

            now = datetime.utcnow()
            period_start = config.last_run_at if config.last_run_at else (now - timedelta(days=1))
            period_end = now

            rule_ids = []
            if config.rule_ids_json:
                try: rule_ids = json.loads(config.rule_ids_json)
                except: pass

            query = db.query(Analysis, Rule).outerjoin(Rule, Analysis.rule_id == Rule.id).filter(
                Analysis.analyzed_at >= period_start,
                Analysis.analyzed_at <= period_end
            )
            if rule_ids:
                query = query.filter(Analysis.rule_id.in_(rule_ids))

            results = query.order_by(desc(Analysis.analyzed_at)).limit(config.max_analyses).all()
            if not results: return {'status': 'ok', 'context': 'Aucun événement en attente dans cette période.', 'analyses_count': 0, 'matched_keywords': []}

            results.reverse()
            compressed_data = []
            all_kws = set()
            for analysis, rule in results:
                if analysis.matched_keywords_json:
                    try: all_kws.update(json.loads(analysis.matched_keywords_json))
                    except: pass
                rule_name = rule.name if rule else 'Inconnue'
                date_str = analysis.analyzed_at.strftime('%Y-%m-%d %H:%M:%S')
                short_ia = analysis.ollama_response.split('\\n\\n')[0][:200] + '...' if analysis.ollama_response else 'N/A'
                block = f"[{date_str}] [SEVERITY: {analysis.severity.upper()}] [Règle: {rule_name}] [ID: {analysis.detection_id}]\\nLigne: {analysis.triggered_line[:500]}\\nIA unitaire: {short_ia}"
                compressed_data.append(block)

            events_text = '\\n\\n'.join(compressed_data)
            prompt = f"{config.system_prompt}\\n\\nVoici les {len(results)} événements (limité aux plus récents) survenus entre {period_start.strftime('%Y-%m-%d %H:%M')} et {period_end.strftime('%Y-%m-%d %H:%M')}:\\n----------------------------------------\\n{events_text}\\n----------------------------------------\\nRéalise une synthèse experte de cette situation globale, croise les informations si plusieurs services sont touchés, et propose des recommandations générales."

            return {'status': 'ok', 'context': prompt, 'analyses_count': len(results), 'matched_keywords': list(all_kws)}
        finally:
            db.close()

    async def execute_meta_analysis(self, config_id: int, trigger_time: datetime = None, custom_context: str = None):
'''
content = content.replace('    async def execute_meta_analysis(self, config_id: int, trigger_time: datetime = None):', new_get_pending)

# 3. Handle custom_context in execute_meta_analysis
old_exec_start = '''        if trigger_time is None:
            trigger_time = datetime.utcnow()

        db = SessionLocal()
        try:
            config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
            if not config:
                return {"status": "error", "message": "Config introuvable"}'''

new_exec_start = '''        if trigger_time is None:
            trigger_time = datetime.utcnow()

        db = SessionLocal()
        try:
            config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
            if not config:
                return {"status": "error", "message": "Config introuvable"}

            global_cfg = db.query(GlobalConfig).first()
            
            period_start = config.last_run_at if config.last_run_at else (trigger_time - timedelta(days=1))
            period_end = trigger_time
            
            prompt = ""
            analyses_count = 0
            detection_ids = []
            all_matched_keywords = set()

            if custom_context:
                prompt = custom_context
                # Dans ce cas on ne re-récupère pas les analyses
                logger.info("MetaAnalysisService", f"Exécution avec contexte édité manuellement pour {config.name}")
            else:
                rule_ids = []
                if config.rule_ids_json:
                    try: rule_ids = json.loads(config.rule_ids_json)
                    except: pass

                query = db.query(Analysis, Rule).outerjoin(Rule, Analysis.rule_id == Rule.id).filter(
                    Analysis.analyzed_at >= period_start, Analysis.analyzed_at <= period_end
                )
                if rule_ids: query = query.filter(Analysis.rule_id.in_(rule_ids))
                results = query.order_by(desc(Analysis.analyzed_at)).limit(config.max_analyses).all()

                if not results:
                    logger.info("MetaAnalysisService", f"Aucune analyse à traiter pour '{config.name}'")
                    config.last_run_at = trigger_time
                    db.commit()
                    return {"status": "skipped", "message": "Aucune donnée"}

                analyses_count = len(results)
                results.reverse()
                compressed_data = []
                for analysis, rule in results:
                    if analysis.detection_id: detection_ids.append(analysis.detection_id)
                    if analysis.matched_keywords_json:
                        try: all_matched_keywords.update(json.loads(analysis.matched_keywords_json))
                        except: pass
                    rule_name = rule.name if rule else "Inconnue"
                    date_str = analysis.analyzed_at.strftime("%Y-%m-%d %H:%M:%S")
                    short_ia = analysis.ollama_response.split("\\n\\n")[0][:200] + "..." if analysis.ollama_response else "N/A"
                    block = f"[{date_str}] [SEVERITY: {analysis.severity.upper()}] [Règle: {rule_name}] [ID: {analysis.detection_id}]\\nLigne: {analysis.triggered_line[:500]}\\nIA unitaire: {short_ia}"
                    compressed_data.append(block)

                events_text = "\\n\\n".join(compressed_data)
                prompt = f"{config.system_prompt}\\n\\nVoici les {analyses_count} événements (limité aux plus récents) survenus entre {period_start.strftime('%Y-%m-%d %H:%M')} et {period_end.strftime('%Y-%m-%d %H:%M')}:\\n----------------------------------------\\n{events_text}\\n----------------------------------------\\nRéalise une synthèse experte de cette situation globale, croise les informations si plusieurs services sont touchés, et propose des recommandations générales."
'''

content = content.replace(old_exec_start, '%%TEMP%%')
content = re.sub(r'%%TEMP%%.*?ollama = self\.orchestrator\.ollama if self\.orchestrator else OllamaService\(\)', new_exec_start + '\n            ollama = self.orchestrator.ollama if self.orchestrator else OllamaService()', content, flags=re.DOTALL)

open('app/services/meta_service.py', 'w', encoding='utf-8').write(content)
print("done")
