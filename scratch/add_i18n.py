import json, os

def update_i18n(filepath, key_path, value):
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    parts = key_path.split('.')
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value
    with open(filepath, 'wb') as f:
        f.write(b'\xef\xbb\xbf')
        f.write(json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8'))
    print(f"Updated {filepath}")

base = os.path.abspath('static/i18n')
update_i18n(os.path.join(base, 'fr.json'), 'common.time_ago', 'Il y a')
update_i18n(os.path.join(base, 'en.json'), 'common.time_ago', 'ago')
