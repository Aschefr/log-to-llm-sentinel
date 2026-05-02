import json
from collections import OrderedDict

paths = [
    r"d:\Code Projects\log-to-llm-sentinel\static\i18n\fr.json",
    r"d:\Code Projects\log-to-llm-sentinel\static\i18n\en.json"
]

keys_to_add = {
    "fr": {
        "theme_light": "Passer au thème clair",
        "theme_dark": "Passer au thème sombre"
    },
    "en": {
        "theme_light": "Switch to light theme",
        "theme_dark": "Switch to dark theme"
    }
}

for path in paths:
    lang = "fr" if "fr.json" in path else "en"
    
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()
    
    # Load with order preservation
    data = json.loads(content, object_pairs_hook=OrderedDict)
    
    if "header" not in data:
        data["header"] = OrderedDict()
        
    data["header"]["theme_light"] = keys_to_add[lang]["theme_light"]
    data["header"]["theme_dark"] = keys_to_add[lang]["theme_dark"]
    
    # Write back preserving BOM (utf-8-sig)
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        # Use indent=4 and ensure_ascii=False
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write('\n') # add trailing newline

print("i18n updated successfully.")
