#!/usr/bin/env python3
"""Add missing kw.packets_estimate to fr.json."""
import json

path = "static/i18n/fr.json"
with open(path, encoding="utf-8-sig") as f:
    data = json.load(f)

data.setdefault("kw", {})["packets_estimate"] = "\u2248 {n} paquet(s) \u00e0 traiter"
print(f"  Set kw.packets_estimate")

with open(path, "w", encoding="utf-8-sig") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("Done.")
