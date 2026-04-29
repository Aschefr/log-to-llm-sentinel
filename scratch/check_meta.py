import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('static/i18n/fr.json', encoding='utf-8-sig') as f:
    d = json.load(f)
meta = d.get('_meta', {})
print(json.dumps(meta, ensure_ascii=False))
# Also check en.json
with open('static/i18n/en.json', encoding='utf-8-sig') as f:
    d2 = json.load(f)
meta2 = d2.get('_meta', {})
print(json.dumps(meta2, ensure_ascii=False))
