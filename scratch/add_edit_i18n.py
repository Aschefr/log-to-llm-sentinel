#!/usr/bin/env python3
"""Add i18n key for monitor.edit_rule."""
import json

keys = {
    "monitor.edit_rule": {"en": "Edit rule", "fr": "Modifier la règle"},
}

for lang in ("en", "fr"):
    path = f"static/i18n/{lang}.json"
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)

    for key, vals in keys.items():
        parts = key.split(".")
        section, subkey = parts[0], parts[1]
        if section not in data:
            data[section] = {}
        if subkey not in data[section]:
            data[section][subkey] = vals[lang]
            print(f"  [{lang}] Added {key}")

    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

print("Done.")
