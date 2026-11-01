from typing import Optional

from app.database import SessionLocal
from app.models import GlobalConfig


def config_to_dict(config: Optional[GlobalConfig]) -> dict:
    """Source unique de la configuration (LLM + notifications) sous forme de dict, avec valeurs par défaut."""
    return {
        "smtp_host": config.smtp_host if config else "",
        "smtp_port": config.smtp_port if config else 587,
        "smtp_user": config.smtp_user if config else "",
        "smtp_password": config.smtp_password if config else "",
        "smtp_recipient": config.smtp_recipient if config else "",
        "smtp_tls": config.smtp_tls if config else True,
        "smtp_ssl_mode": config.smtp_ssl_mode if config else "starttls",
        "ollama_url": config.ollama_url if config and config.ollama_url else "http://ollama:11434",
        "ollama_model": config.ollama_model if config and config.ollama_model else "gemma4:e4b",
        "ollama_temp": config.ollama_temp if config and config.ollama_temp is not None else 0.1,
        "ollama_ctx": config.ollama_ctx if config and config.ollama_ctx else 4096,
        "ollama_think": config.ollama_think if config and config.ollama_think is not None else True,
        "system_prompt": config.system_prompt if config else "",
        "notification_method": config.notification_method if config else "smtp",
        "apprise_url": config.apprise_url if config else "",
        "apprise_tags": config.apprise_tags if config else "",
        "apprise_max_chars": config.apprise_max_chars if config else 1900,
        "max_log_chars": config.max_log_chars if config else 5000,
        "monitor_log_lines": config.monitor_log_lines if config else 60,
        "debug_mode": config.debug_mode if config else False,
        "ollama_prompt_lang": (config.ollama_prompt_lang or 'fr') if config else 'fr',
        "site_lang": (config.site_lang or 'fr') if config else 'fr',
        "instance_name": (config.instance_name or '') if config else '',
        "discord_webhook_url": (config.discord_webhook_url or '') if config else '',
        "auto_delete_analyses": config.auto_delete_analyses if config else False,
        "auto_delete_retention_days": config.auto_delete_retention_days if config else 30,
        "syslog_enabled": config.syslog_enabled if config else False,
        "syslog_forward_addr": (config.syslog_forward_addr or '') if config else '',
    }


def load_config_dict() -> dict:
    db = SessionLocal()
    try:
        return config_to_dict(db.query(GlobalConfig).first())
    finally:
        db.close()


def llm_options(config: dict, **overrides) -> dict:
    """Options Ollama standard. Garder le même num_ctx partout évite qu'Ollama recharge le modèle."""
    opts = {"temperature": config.get("ollama_temp", 0.1), "num_ctx": config.get("ollama_ctx", 4096)}
    opts.update(overrides)
    return opts
