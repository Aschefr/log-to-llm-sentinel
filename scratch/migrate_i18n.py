import json
import os
import sys

sys.path.append(os.path.abspath("."))
from app.utils.notification_i18n import _TRANSLATIONS

for lang in ["fr", "en"]:
    filepath = f"static/i18n/{lang}.json"
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    
    data["notifications"] = _TRANSLATIONS[lang]
    
    with open(filepath, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write('\n')
