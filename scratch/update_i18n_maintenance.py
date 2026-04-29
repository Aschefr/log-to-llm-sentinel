import json
import os

def update_json(filepath, new_keys):
    encoding = 'utf-8-sig'
    with open(filepath, 'r', encoding=encoding) as f:
        data = json.load(f)
    
    # Nested updates could be handled but here we use simple keys like 'config.maintenance_title'
    # Wait, the i18n.js uses dot notation natively, but is the JSON flat or nested?
    # Let's check how the JSON is structured. Usually it's nested if it has dots, or flat with dots in keys.
    # We will assume it's nested because keys like "config": { "title": ... } are standard.
    
    if "config" not in data:
        data["config"] = {}
        
    for k, v in new_keys.items():
        if k.startswith("config."):
            sub_key = k.split(".", 1)[1]
            data["config"][sub_key] = v
        else:
            data[k] = v
    
    with open(filepath, 'w', encoding=encoding, newline='') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write('\n')
    print(f"Updated {filepath}")

en_keys = {
    "config.maintenance_title": "Maintenance & Storage",
    "config.auto_delete_analyses": "Automatically delete old data",
    "config.retention_period": "Retention Period",
    "config.retention_1w": "1 week",
    "config.retention_1m": "1 month",
    "config.retention_6m": "6 months",
    "config.retention_1y": "1 year",
    "config.retention_custom": "Custom...",
    "config.disk_usage_title": "Disk Usage (Data Folder)",
    "config.found_old_items": "Old items found (> <span id=\"cleanup-days-val\">30</span> days) : ",
    "config.cleanup_btn": "Clean up now",
    "config.cleanup_confirm": "Are you sure you want to permanently delete this data?"
}

fr_keys = {
    "config.maintenance_title": "Maintenance & Stockage",
    "config.auto_delete_analyses": "Suppression automatique des anciennes données",
    "config.retention_period": "Période de conservation",
    "config.retention_1w": "1 semaine",
    "config.retention_1m": "1 mois",
    "config.retention_6m": "6 mois",
    "config.retention_1y": "1 an",
    "config.retention_custom": "Personnalisé...",
    "config.disk_usage_title": "Espace Disque (Dossier Data)",
    "config.found_old_items": "Éléments anciens trouvés (> <span id=\"cleanup-days-val\">30</span> jours) : ",
    "config.cleanup_btn": "Nettoyer maintenant",
    "config.cleanup_confirm": "Êtes-vous sûr de vouloir supprimer définitivement ces données ?"
}

base_dir = r"d:\Code Projects\log-to-llm-sentinel\static\i18n"
update_json(os.path.join(base_dir, "en.json"), en_keys)
update_json(os.path.join(base_dir, "fr.json"), fr_keys)
