import json
import os

def update_i18n(filepath, new_keys):
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    
    if 'config' not in data:
        data['config'] = {}
        
    for k, v in new_keys.items():
        data['config'][k] = v
        
    with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write('\n')

fr_keys = {
    "method_discord": "Discord Webhook",
    "discord_title": "Configuration Discord",
    "discord_webhook": "URL Webhook Discord",
    "discord_webhook_placeholder": "https://discord.com/api/webhooks/...",
    "discord_help": "Collez l'URL du Webhook depuis les paramètres d'intégration de votre salon Discord.",
    "test_discord": "Tester Discord"
}

en_keys = {
    "method_discord": "Discord Webhook",
    "discord_title": "Discord Configuration",
    "discord_webhook": "Discord Webhook URL",
    "discord_webhook_placeholder": "https://discord.com/api/webhooks/...",
    "discord_help": "Paste the Webhook URL from your Discord channel integration settings.",
    "test_discord": "Test Discord"
}

update_i18n('d:/Code Projects/log-to-llm-sentinel/static/i18n/fr.json', fr_keys)
update_i18n('d:/Code Projects/log-to-llm-sentinel/static/i18n/en.json', en_keys)
print("i18n updated.")
