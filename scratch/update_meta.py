filepath = 'app/services/meta_service.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add import
content = content.replace(
    'from app import logger\n',
    'from app import logger\nfrom app.utils.notification_i18n import nt\n',
    1
)

# 2. Replace _send_notification method body labels
# Subject
content = content.replace(
    'subject = f"[Sentinel] Méta-Analyse : {config.name}"',
    "subject = nt('meta_subject', lang).format(config_name=config.name)",
    1
)

# We need to add lang resolution before subject
content = content.replace(
    "        notifier = NotificationService()\n        \n        subject = nt('meta_subject', lang)",
    "        notifier = NotificationService()\n        lang = global_cfg.site_lang or 'fr'\n        \n        subject = nt('meta_subject', lang)",
    1
)

# SMTP body
content = content.replace(
    'Méta-Analyse Sentinel : {config.name}</h2>',
    "{nt('meta_title', lang)} : {config.name}</h2>",
    1
)
content = content.replace(
    '<p><strong>Période:</strong>',
    "<p><strong>{nt('period', lang)}:</strong>",
    1
)
content = content.replace(
    '<p><strong>Événements analysés:</strong>',
    "<p><strong>{nt('events_analyzed', lang)}:</strong>",
    1
)
content = content.replace(
    '<h3>Synthèse IA :</h3>',
    "<h3>{nt('ia_synthesis', lang)} :</h3>",
    1
)

# Apprise body
content = content.replace(
    'Méta-Analyse : {config.name}\n**Période:**',
    "{nt('meta_title', lang)} : {config.name}\n**{nt('period', lang)}:**",
    1
)
content = content.replace(
    '**Événements:** {result.analyses_count}',
    "**{nt('events_analyzed', lang)}:** {result.analyses_count}",
    1
)
content = content.replace(
    '**Synthèse:**',
    "**{nt('ia_synthesis', lang)}:**",
    1
)

# Apprise summary prompt (the hardcoded FR one)
old_meta_summary = '''                summary_prompt = (
                    f"Résume la méta-analyse suivante de manière très lisible pour une notification mobile (Discord/Telegram).\\n"
                    f"Conserve les tendances principales et les points critiques.\\n"
                    f"Utilise des puces (bullet points).\\n"
                    f"Limite-toi à {max_chars - 500} caractères maximum.\\n\\n"
                    f"Analyse à résumer :\\n{result.ollama_response}"
                )'''
new_meta_summary = "                summary_prompt = nt('meta_summary_prompt', lang).format(max_chars=max_chars - 500, response=result.ollama_response)"
content = content.replace(old_meta_summary, new_meta_summary, 1)

# Apprise summary result labels
content = content.replace(
    'Méta-Analyse (Résumé) : {config.name}',
    "{nt('meta_title', lang)} ({nt('summary_of_synthesis', lang)}) : {config.name}",
    1
)
content = content.replace(
    '**Événements:** {result.analyses_count}\n\n**Résumé de la synthèse:**',
    "**{nt('events_analyzed', lang)}:** {result.analyses_count}\n\n**{nt('summary_of_synthesis', lang)}:**",
    1
)
content = content.replace(
    "*(Synthèse complète dans l'interface)*",
    "{nt('full_synthesis_available', lang)}",
    1
)

# Second occurrence of **Période:** in summary result
content = content.replace(
    '**Période:** {result.period_start',
    "**{nt('period', lang)}:** {result.period_start",
    1
)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

# Validation
with open(filepath, 'r', encoding='utf-8') as f:
    c = f.read()

checks = [
    'from app.utils.notification_i18n import nt',
    "nt('meta_subject', lang)",
    "nt('meta_title', lang)",
    "nt('period', lang)",
    "nt('events_analyzed', lang)",
    "nt('ia_synthesis', lang)",
    "nt('meta_summary_prompt', lang)",
    "nt('full_synthesis_available', lang)",
    "global_cfg.site_lang",
]
for check in checks:
    assert check in c, f"MISSING: {check}"

count = c.count("nt(")
print(f"meta_service.py updated successfully! Found {count} nt() calls")
