#!/usr/bin/env python3
"""Fix kw.card_scanning mojibake and re-scan ALL values exhaustively."""
import json, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PATH = "static/i18n/fr.json"

with open(PATH, encoding="utf-8-sig") as f:
    data = json.load(f)

def try_fix(val):
    """Try to recover mojibake by re-encoding as latin-1 or cp1252 then decoding as UTF-8."""
    for enc in ('latin-1', 'cp1252'):
        try:
            return val.encode(enc).decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return None

def is_mojibake(val):
    """Check if a string looks like mojibake (can be re-encoded to valid UTF-8)."""
    if not isinstance(val, str):
        return False
    for enc in ('latin-1', 'cp1252'):
        try:
            recovered = val.encode(enc).decode('utf-8')
            if recovered != val:
                return True
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return False

# Exhaustive scan
issues = []
def scan(obj, prefix=''):
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            scan(v, key)
        elif isinstance(v, str) and is_mojibake(v):
            fixed = try_fix(v)
            issues.append((key, v, fixed))

scan(data)

if not issues:
    print("✅ No mojibake found!")
    sys.exit(0)

print(f"⚠️  Found {len(issues)} mojibake value(s):\n")
for key, bad, fixed in issues:
    print(f"  🔴 {key}")
    print(f"     Bad : {bad}")
    print(f"     Fix : {fixed}")
    print()

# Auto-fix all
fixed_count = 0
for key, bad, fixed in issues:
    if not fixed:
        continue
    parts = key.split('.')
    obj = data
    for p in parts[:-1]:
        obj = obj[p]
    obj[parts[-1]] = fixed
    fixed_count += 1

with open(PATH, 'w', encoding='utf-8-sig') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write('\n')
print(f"✅ Auto-fixed {fixed_count} value(s) and saved {PATH}")
