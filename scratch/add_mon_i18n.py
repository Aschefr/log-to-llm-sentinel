#!/usr/bin/env python3
"""Add i18n keys for MON-09 through MON-12."""
import json, sys

keys = {
    "monitor.add_rule":         {"en": "Add rule",            "fr": "Ajouter une règle"},
    "monitor.show_more":        {"en": "Show more",           "fr": "Afficher plus"},
    "monitor.no_more_analyses": {"en": "No more analyses",    "fr": "Pas d'autres analyses"},
    "monitor.autolearn_title":  {"en": "Auto-learning",       "fr": "Auto-apprentissage"},
}

for lang in ("en", "fr"):
    path = f"static/i18n/{lang}.json"
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)

    changed = False
    for key, vals in keys.items():
        parts = key.split(".")
        section = parts[0]
        subkey = ".".join(parts[1:])

        if section not in data:
            data[section] = {}
        if subkey not in data[section]:
            data[section][subkey] = vals[lang]
            changed = True
            print(f"  [{lang}] Added {key} = {vals[lang]}")

    if changed:
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  [{lang}] Saved {path}")
    else:
        print(f"  [{lang}] No changes needed")

print("Done.")
