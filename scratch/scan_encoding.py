#!/usr/bin/env python3
"""
Scan fr.json for mojibake / encoding corruption.

Detects:
  1. Mojibake: UTF-8 bytes mis-decoded as Latin-1 (e.g. â‰ˆ instead of ≈)
  2. Replacement chars: \uFFFD (�)
  3. Suspicious C1 control-like sequences (bytes 0x80-0x9F decoded as cp1252)
  4. Broken emoji: 4-byte UTF-8 sequences decoded as Latin-1 (ðŸ...)

Usage:  python scratch/scan_encoding.py
"""
import json, re, sys

# Force UTF-8 console output on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PATH = "static/i18n/fr.json"

# Common mojibake signatures: Latin-1 interpretation of UTF-8 lead bytes
# UTF-8 2-byte: C2-DF lead → Latin-1 Â-ß  followed by 80-BF → Latin-1 various
# UTF-8 3-byte: E0-EF lead → Latin-1 à-ï  followed by 2x (80-BF)
# UTF-8 4-byte: F0-F4 lead → Latin-1 ð-ô  followed by 3x (80-BF)
MOJIBAKE_RE = re.compile(
    r'[\xc2-\xdf][\x80-\xbf]'       # 2-byte UTF-8 as Latin-1
    r'|[\xe0-\xef][\x80-\xbf]{2}'   # 3-byte
    r'|[\xf0-\xf4][\x80-\xbf]{3}'   # 4-byte (broken emoji)
)

# Known suspicious patterns that appear in mojibake
SUSPECT_FRAGMENTS = [
    'Ã©',  # é
    'Ã¨',  # è
    'Ãª',  # ê
    'Ã ',  # à
    'Ã¢',  # â
    'Ã®',  # î
    'Ã´',  # ô
    'Ã¹',  # ù
    'Ã»',  # û
    'Ã§',  # ç
    'Ã‰',  # É
    'Ã€',  # À
    'Ã"',  # Ô
    'Â«',  # «
    'Â»',  # »
    'Â ',  # non-breaking space
    'â€™', # '
    'â€œ', # "
    'â€\x9d', # "
    'â€"', # —
    'â€"', # –
    'â‰ˆ', # ≈
    'ðŸ',  # broken emoji lead
]

def try_fix(bad_value):
    """Attempt to recover original text by re-encoding as Latin-1 then decoding as UTF-8."""
    try:
        return bad_value.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    try:
        return bad_value.encode('cp1252').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return None

def scan_value(key, value, issues):
    if not isinstance(value, str):
        return
    
    # Check for replacement character
    if '\ufffd' in value:
        issues.append((key, 'REPLACEMENT_CHAR', value, None))
    
    # Check for known mojibake fragments
    for frag in SUSPECT_FRAGMENTS:
        if frag in value:
            fixed = try_fix(value)
            issues.append((key, f'MOJIBAKE_FRAGMENT "{frag}"', value, fixed))
            return  # one report per value
    
    # Check for mojibake regex pattern (catches remaining cases)
    # Re-encode value to bytes to test
    try:
        raw = value.encode('utf-8')
        # If value contains characters in the C2-F4 range followed by 80-BF,
        # it might be double-encoded. Test by encoding as latin-1.
        test = value.encode('latin-1')
        try:
            recovered = test.decode('utf-8')
            if recovered != value:
                issues.append((key, 'DOUBLE_ENCODED', value, recovered))
                return
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    except (UnicodeEncodeError):
        pass  # Contains chars outside Latin-1 → likely fine (real Unicode)


def scan_dict(data, prefix, issues):
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            scan_dict(v, full_key, issues)
        elif isinstance(v, str):
            scan_value(full_key, v, issues)


def main():
    with open(PATH, encoding='utf-8-sig') as f:
        data = json.load(f)

    issues = []
    scan_dict(data, '', issues)

    if not issues:
        print(f"✅  No encoding issues found in {PATH}")
        return 0

    print(f"⚠️  Found {len(issues)} encoding issue(s) in {PATH}:\n")
    for key, issue_type, bad, fixed in issues:
        print(f"  🔴 [{issue_type}]  {key}")
        print(f"     Current : {repr(bad)}")
        if fixed:
            print(f"     Fix     : {repr(fixed)}")
        print()

    # Ask to auto-fix
    answer = input(f"Auto-fix {len([i for i in issues if i[3]])} fixable issues? [y/N] ").strip().lower()
    if answer == 'y':
        fixed_count = 0
        for key, _, bad, fixed in issues:
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
        print(f"\n✅ Fixed {fixed_count} issue(s) and saved {PATH}")
    else:
        print("\nNo changes made.")

    return 1


if __name__ == '__main__':
    sys.exit(main())
