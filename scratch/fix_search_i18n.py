#!/usr/bin/env python3
"""Fix garbled emoji in fr.json search_placeholder."""
import json

path = "static/i18n/fr.json"
with open(path, encoding="utf-8-sig") as f:
    data = json.load(f)

old = data.get("monitor", {}).get("search_placeholder", "")
print(f"Old value: {repr(old)}")

data["monitor"]["search_placeholder"] = "\U0001f50d Rechercher par ID de d\u00e9tection..."

with open(path, "w", encoding="utf-8-sig") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("Fixed.")
