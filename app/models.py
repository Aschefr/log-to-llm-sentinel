from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime

from app.database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    log_file_path = Column(String, nullable=False)
    keywords_json = Column(Text, default="[]")
    application_context = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    notify_on_match = Column(Boolean, default=True)
    context_lines = Column(Integer, default=5)
    anti_spam_delay = Column(Integer, default=60)
    notify_severity_threshold = Column(String, default="info")
    last_position = Column(Float, default=0.0)
    last_learning_session_id = Column(Integer, nullable=True)  # dernière session d'auto-apprentissage
    excluded_patterns_json = Column(Text, default="[]")  # patterns d'exclusion (negative keywords)
    last_line_received_at = Column(DateTime, nullable=True)
    inactivity_warning_enabled = Column(Boolean, default=True)
    inactivity_period_hours = Column(Integer, default=1)
    inactivity_notify = Column(Boolean, default=True)
    inactivity_notified = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def get_keywords(self):
        import json
        try:
            return json.loads(self.keywords_json) if self.keywords_json else []
        except json.JSONDecodeError:
            return []

    def set_keywords(self, keywords):
        import json
        self.keywords_json = json.dumps(keywords)

    def get_excluded_patterns(self):
        import json
        try:
            return json.loads(self.excluded_patterns_json) if self.excluded_patterns_json else []
        except json.JSONDecodeError:
            return []

    def set_excluded_patterns(self, patterns):
        import json
        self.excluded_patterns_json = json.dumps(patterns)


class Analysis(Base):
    __tablename__ = "analyses"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, index=True)
    detection_id = Column(String, index=True, nullable=True)  # UUID court de détection
    triggered_line = Column(Text, nullable=False)
    matched_keywords_json = Column(Text, default="[]")  # mots-clés ayant déclenché la règle
    context_before_json = Column(Text, default="[]")
    context_after_json = Column(Text, default="[]")
    ollama_response = Column(Text)
    severity = Column(String, default="info")
    notified = Column(Boolean, default=False)
    analyzed_at = Column(DateTime, server_default=func.now())


class GlobalConfig(Base):
    __tablename__ = "global_config"

    id = Column(Integer, primary_key=True, index=True)
    smtp_host = Column(String, default="")
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String, default="")
    smtp_password = Column(String, default="")
    smtp_recipient = Column(String, default="")
    smtp_tls = Column(Boolean, default=True)  # legacy
    smtp_ssl_mode = Column(String, default="starttls")  # 'ssl' | 'starttls' | 'none'
    ollama_url = Column(String, default="http://ollama:11434")
    ollama_model = Column(String, default="gemma4:e4b")
    ollama_temp = Column(Float, default=0.1)
    ollama_ctx = Column(Integer, default=4096)
    ollama_think = Column(Boolean, default=True)
    system_prompt = Column(Text, default="")
    notification_method = Column(String, default="smtp")
    apprise_url = Column(String, nullable=True)
    apprise_tags = Column(String, nullable=True)
    apprise_max_chars = Column(Integer, default=1900)
    max_log_chars = Column(Integer, default=5000)
    monitor_log_lines = Column(Integer, default=60)
    debug_mode = Column(Boolean, default=False)
    ollama_prompt_lang = Column(String, default='fr')  # 'fr' | 'en' — langue des prompts d'analyse
    site_lang = Column(String, default='fr')  # langue du site (header) — utilisée pour les notifications
    instance_name = Column(String, default='')  # nom de l'instance pour différencier les notifications multi-déploiement
    chat_system_prompt = Column(Text, default="")
    chat_lang = Column(String, default="")  # Default header lang if empty
    auto_delete_analyses = Column(Boolean, default=False)
    auto_delete_retention_days = Column(Integer, default=30)
    discord_webhook_url = Column(String, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    analysis_id = Column(Integer, ForeignKey("analyses.id"), nullable=True)
    
    # Compression fields
    compression_mode = Column(String, nullable=True) # 'compact', 'summary'
    compressed_context = Column(Text, nullable=True)
    compressed_at = Column(DateTime, nullable=True)
    
    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id"))
    role = Column(String) # user, assistant
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("ChatConversation", back_populates="messages")

class MetaAnalysisConfig(Base):
    __tablename__ = "meta_analysis_configs"
    id = Column(Integer, primary_key=True, index=True)
    rule_ids_json = Column(Text, default="[]")  # List of rule IDs to include. Empty means all rules.
    name = Column(String, nullable=False)
    schedule_type = Column(String, default="daily") # 'daily', 'weekly', 'monthly'
    schedule_time = Column(String, default="00:00") # 'HH:MM'
    schedule_day = Column(Integer, default=1)       # 1-7 for weekly, 1-31 for monthly
    enabled = Column(Boolean, default=True)
    notify_enabled = Column(Boolean, default=True)
    context_size = Column(Integer, default=16000)
    system_prompt = Column(Text, default="Tu es un expert DevOps. Analyse ces événements et fais une synthèse globale de la situation du service.")
    max_analyses = Column(Integer, default=50)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MetaAnalysisResult(Base):
    __tablename__ = "meta_analysis_results"
    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(Integer, ForeignKey("meta_analysis_configs.id", ondelete="CASCADE"))
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    analyses_count = Column(Integer, default=0)
    detection_ids_json = Column(Text, default="[]")  # List of detection IDs included
    matched_keywords_json = Column(Text, default="[]")  # List of all keywords matched across all included analyses
    context_sent = Column(Text, nullable=True)  # Prompt exact envoyé au LLM
    ollama_response = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class KeywordLearningSession(Base):
    __tablename__ = "keyword_learning_sessions"
    id                     = Column(Integer, primary_key=True, index=True)
    rule_id                = Column(Integer, nullable=True)          # null avant création de la règle
    log_file_path          = Column(String)
    period_start           = Column(DateTime)                        # UTC
    period_end             = Column(DateTime)                        # UTC
    granularity_s          = Column(Integer)                         # secondes par paquet
    max_chars_per_packet   = Column(Integer, default=5000)
    status                 = Column(String, default="pending")       # pending|scanning|refining|validated|reverted|error
    total_packets          = Column(Integer, default=0)
    completed_packets      = Column(Integer, default=0)
    raw_keywords_json      = Column(Text, default="[]")              # candidats bruts accumulés
    final_keywords_json    = Column(Text, default="[]")              # après raffinement
    raw_exclusions_json    = Column(Text, default="[]")              # exclusions brutes
    final_exclusions_json  = Column(Text, default="[]")              # exclusions retenues
    previous_keywords_json = Column(Text, default="[]")             # keywords avant learning (revert)
    refine_rationale_json  = Column(Text, default="{}")             # {keyword: raison}
    ollama_log_json        = Column(Text, default="[]")              # [{packet_idx, window, chars, keywords}]
    error_message          = Column(Text, nullable=True)
    created_at             = Column(DateTime, default=datetime.utcnow)
    validated_at           = Column(DateTime, nullable=True)
