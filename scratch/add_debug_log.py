filepath = 'app/services/orchestrator.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Add debug log for lang after it's set
old_lang = 'lang = config.get("site_lang", config.get("ollama_prompt_lang", "fr"))\n        det_id_label'
new_lang = 'lang = config.get("site_lang", config.get("ollama_prompt_lang", "fr"))\n        logger.debug("Orchestrator", f"Notification lang={lang} (site_lang={config.get(\'site_lang\')}, ollama_prompt_lang={config.get(\'ollama_prompt_lang\')})")\n        det_id_label'

content = content.replace(old_lang, new_lang, 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Debug log added to orchestrator.py")
