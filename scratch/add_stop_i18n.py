#!/usr/bin/env python3
"""Add missing kw.stop_btn i18n key."""
import json

keys = {
    "kw.stop_btn": {"en": "Stop", "fr": "Arrêter"},
}

for lang in ("en", "fr"):
    path = f"static/i18n/{lang}.json"
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)

    for key, vals in keys.items():
        section, subkey = key.split(".")
        if section not in data:
            data[section] = {}
        if subkey not in data[section]:
            data[section][subkey] = vals[lang]
            print(f"  [{lang}] Added {key} = {vals[lang]}")

    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

print("Done.")
