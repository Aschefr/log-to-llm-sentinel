import json
import os

def update_json(filepath, new_keys):
    encoding = 'utf-8-sig'
    with open(filepath, 'r', encoding=encoding) as f:
        data = json.load(f)
    
    if "monitor" not in data:
        data["monitor"] = {}
        
    for k, v in new_keys.items():
        if k.startswith("monitor."):
            sub_key = k.split(".", 1)[1]
            data["monitor"][sub_key] = v
        else:
            data[k] = v
    
    with open(filepath, 'w', encoding=encoding, newline='') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write('\n')
    print(f"Updated {filepath}")

en_keys = {
    "monitor.delete_rule_confirm": "Are you sure you want to delete this rule? This will also remove associated analyses.",
}

fr_keys = {
    "monitor.delete_rule_confirm": "Êtes-vous sûr de vouloir supprimer cette règle ? Cela supprimera également les analyses associées.",
}

base_dir = r"d:\Code Projects\log-to-llm-sentinel\static\i18n"
update_json(os.path.join(base_dir, "en.json"), en_keys)
update_json(os.path.join(base_dir, "fr.json"), fr_keys)
