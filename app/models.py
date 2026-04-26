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
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    analysis_id = Column(Integer, ForeignKey("analyses.id"), nullable=True)
    
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
    interval_hours = Column(Integer, default=24)
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
    ollama_response = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
