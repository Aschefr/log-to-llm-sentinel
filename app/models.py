from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text
from sqlalchemy.sql import func

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
    triggered_line = Column(Text, nullable=False)
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
    smtp_tls = Column(Boolean, default=True)
    ollama_url = Column(String, default="http://host.docker.internal:11434")
    ollama_model = Column(String, default="llama3")
    system_prompt = Column(Text, default="")
    notification_method = Column(String, default="smtp")
    apprise_url = Column(String, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
