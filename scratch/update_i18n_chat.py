import json
import os

def update_lang_file(path, updates):
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    
    # Update nested dict for chat
    if "chat" not in data:
        data["chat"] = {}
        
    for k, v in updates.items():
        data["chat"][k] = v
        
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        # Add newline at end of file if missing
        f.write('\n')

fr_updates = {
    "settings_title": "Paramètres du Chat",
    "lang_select": "Langue des requêtes (Chat)",
    "lang_site_default": "Par défaut (Langue du site)",
    "system_prompt": "Prompt système",
    "btn_translate": "Traduire auto.",
    "btn_reset": "Réinitialiser",
    "btn_save": "Enregistrer",
    "btn_cancel": "Annuler",
    "context_preview_title": "Contexte auto-ajouté (Aperçu)",
    "settings_btn": "Paramètres ⚙️"
}

en_updates = {
    "settings_title": "Chat Settings",
    "lang_select": "Query Language (Chat)",
    "lang_site_default": "Default (Site Language)",
    "system_prompt": "System Prompt",
    "btn_translate": "Auto-translate",
    "btn_reset": "Reset",
    "btn_save": "Save",
    "btn_cancel": "Cancel",
    "context_preview_title": "Auto-appended context (Preview)",
    "settings_btn": "Settings ⚙️"
}

try:
    update_lang_file('static/i18n/fr.json', fr_updates)
    print("Updated fr.json")
    update_lang_file('static/i18n/en.json', en_updates)
    print("Updated en.json")
except Exception as e:
    print(f"Error: {e}")
