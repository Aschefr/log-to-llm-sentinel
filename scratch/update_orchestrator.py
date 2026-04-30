import re

filepath = 'app/services/orchestrator.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

content = ''.join(lines)

# 1. Add import
content = content.replace(
    'from app import logger\n',
    'from app import logger\nfrom app.utils.notification_i18n import nt\n',
    1
)

# 2. Add site_lang to config_dict
content = content.replace(
    '"debug_mode": config.debug_mode if config else False,\n        }',
    '"debug_mode": config.debug_mode if config else False,\n            "site_lang": (config.site_lang or "fr") if config else "fr",\n        }',
    1
)

# 3. Add lang = config.get("site_lang"...) before det_id_label
content = content.replace(
    '        det_id_label = f" [ID: {detection_id}]" if detection_id else ""\n        logger.debug("Orchestrator"',
    '        lang = config.get("site_lang", config.get("ollama_prompt_lang", "fr"))\n        det_id_label = f" [ID: {detection_id}]" if detection_id else ""\n        logger.debug("Orchestrator"',
    1
)

# 4. Replace subject line
content = content.replace(
    'subject = f"[Sentinel] Alerte {severity.upper()} : {rule.name}{det_id_label}"',
    "subject = f\"[Sentinel] {nt('alert', lang)} {severity.upper()} : {rule.name}{det_id_label}\"",
    1
)

# 5. Replace SMTP body labels
content = content.replace('Alerte Log to LLM Sentinel</h2>', "{nt('alert_title', lang)}</h2>", 1)
content = content.replace('<p><strong>Règle:</strong>', "<p><strong>{nt('rule', lang)}:</strong>", 1)
content = content.replace("<p><strong>ID de détection:</strong>", "<p><strong>{nt('detection_id', lang)}:</strong>", 1)
content = content.replace("<p><strong>Mots-clés:</strong>", "<p><strong>{nt('keywords', lang)}:</strong>", 1)
content = content.replace("<p><strong>Ligne déclenchante:</strong></p>", "<p><strong>{nt('triggered_line', lang)}:</strong></p>", 1)
content = content.replace("<p><strong>Analyse Ollama:</strong></p>", "<p><strong>{nt('ollama_analysis', lang)}:</strong></p>", 1)
content = content.replace("<p><strong>Sévérité:</strong>", "<p><strong>{nt('severity', lang)}:</strong>", 1)

# 6. Replace Apprise body labels
content = content.replace(
    'Alerte Sentinel : {rule.name}',
    "{nt('alert', lang)} Sentinel : {rule.name}",
    1
)
content = content.replace('**Sévérité:**', "**{nt('severity', lang)}:**", 1)
content = content.replace('**Mots-clés:**', "**{nt('keywords', lang)}:**", 1)
content = content.replace('**Ligne:**', "**{nt('triggered_line', lang)}:**", 1)
content = content.replace('**Analyse Ollama:**', "**{nt('ollama_analysis', lang)}:**", 1)

# 7. Replace summary prompt section (the if lang == 'en' / else block)
old_summary_block = '''            if lang == 'en':
                summary_prompt = (
                    f"Summarize the following log analysis for a mobile notification (Discord/Telegram).\\n"
                    f"Use bullet points and clear sections (Problem, Cause, Fix).\\n"
                    f"Limit to {max_chars - 500} characters maximum.\\n\\n"
                    f"Analysis:\\n{response}"
                )
            else:
                summary_prompt = (
                    f"Résume l'analyse suivante de manière très lisible pour une notification mobile (Discord/Telegram).\\n"
                    f"Utilise des puces (bullet points) et des sections claires (Problème, Cause, Solution).\\n"
                    f"Limite-toi à {max_chars - 500} caractères maximum.\\n\\n"
                    f"Analyse à résumer :\\n{response}"
                )'''

new_summary_block = '''            summary_prompt = nt('summary_prompt', lang).format(max_chars=max_chars - 500, response=response)'''

content = content.replace(old_summary_block, new_summary_block, 1)

# 8. Replace summary result body labels
content = content.replace(
    'Alerte Sentinel (Résumé)',
    "{nt('alert', lang)} Sentinel ({nt('analysis_summary', lang)})",
    1
)
# The "Sévérité" label in summary body
content = content.replace(
    '**Sévérité:** {severity.upper()}',
    "**{nt('severity', lang)}:** {severity.upper()}",
    1  # only the second occurrence (first already replaced)
)
content = content.replace(
    "**Résumé de l'analyse :**",
    "**{nt('analysis_summary', lang)} :**",
    1
)
content = content.replace(
    "*(Analyse complète disponible dans l'interface)*",
    "{nt('full_analysis_available', lang)}",
    1
)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

# Quick validation
with open(filepath, 'r', encoding='utf-8') as f:
    c = f.read()

checks = [
    'from app.utils.notification_i18n import nt',
    "nt('alert', lang)",
    "nt('alert_title', lang)",
    "nt('rule', lang)",
    "nt('severity', lang)",
    "nt('triggered_line', lang)",
    "site_lang",
    "nt('summary_prompt', lang)",
]
for check in checks:
    assert check in c, f"MISSING: {check}"

print("orchestrator.py updated successfully!")
print(f"Found {c.count('nt(')} nt() calls")
