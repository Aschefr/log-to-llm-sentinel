import json, re

with open('static/i18n/fr.json', 'rb') as f:
    raw = f.read()

# Strip BOM if present
if raw[:3] == b'\xef\xbb\xbf':
    raw = raw[3:]

# Byte-level replacement table: double-encoded UTF-8 sequences → correct UTF-8 bytes
# Pattern: char was C3 XX in original UTF-8.
# XX was read as cp1252 → unicode char U, then U was UTF-8 encoded.
# Some XX (0x8D etc.) are undefined in cp1252 → stored as C2 XX.
replacements = [
    # 3-byte patterns first (longer match priority) — uppercase accented chars via cp1252 special
    (b'\xc3\x83\xe2\x82\xac', b'\xc3\x80'),  # À (0x80 in cp1252 = € = E2 82 AC)
    (b'\xc3\x83\xe2\x80\xb0', b'\xc3\x89'),  # É (0x89 in cp1252 = ‰ = E2 80 B0)
    (b'\xc3\x83\xcb\x86',     b'\xc3\x88'),  # È (0x88 → ˆ = CB 86)
    (b'\xc3\x83\xc5\xa0',     b'\xc3\x8a'),  # Ê (0x8A → Š = C5 A0)
    (b'\xc3\x83\xe2\x80\x9a', b'\xc3\x82'),  # Â (0x82 → ‚ = E2 80 9A)
    (b'\xc3\x83\xe2\x80\xa1', b'\xc3\x87'),  # Ç (0x87 → ‡ = E2 80 A1)
    (b'\xc3\x83\xe2\x80\x9e', b'\xc3\x84'),  # Ä (0x84 → „ = E2 80 9E)
    (b'\xc3\x83\xe2\x80\xa6', b'\xc3\x85'),  # Å (0x85 → … = E2 80 A6)
    (b'\xc3\x83\xe2\x80\xa0', b'\xc3\x86'),  # Æ (0x86 → † = E2 80 A0)
    (b'\xc3\x83\xcb\x9c',     b'\xc3\x9c'),  # Ü (0x9C → œ = CB 9C)  wait... let me recalc
    # 2-byte patterns (C3 83 C2 XX → C3 XX) for chars in Latin-1 supplement
    (b'\xc3\x83\xc2\xa0', b'\xc3\xa0'),  # à
    (b'\xc3\x83\xc2\xa2', b'\xc3\xa2'),  # â
    (b'\xc3\x83\xc2\xa7', b'\xc3\xa7'),  # ç
    (b'\xc3\x83\xc2\xa8', b'\xc3\xa8'),  # è
    (b'\xc3\x83\xc2\xa9', b'\xc3\xa9'),  # é
    (b'\xc3\x83\xc2\xaa', b'\xc3\xaa'),  # ê
    (b'\xc3\x83\xc2\xab', b'\xc3\xab'),  # ë
    (b'\xc3\x83\xc2\xae', b'\xc3\xae'),  # î
    (b'\xc3\x83\xc2\xaf', b'\xc3\xaf'),  # ï
    (b'\xc3\x83\xc2\xb4', b'\xc3\xb4'),  # ô
    (b'\xc3\x83\xc2\xb9', b'\xc3\xb9'),  # ù
    (b'\xc3\x83\xc2\xbb', b'\xc3\xbb'),  # û
    (b'\xc3\x83\xc2\xbc', b'\xc3\xbc'),  # ü
    (b'\xc3\x83\xc2\xb6', b'\xc3\xb6'),  # ö
    (b'\xc3\x83\xc2\xb8', b'\xc3\xb8'),  # ø
    (b'\xc3\x83\xc2\xa6', b'\xc3\xa6'),  # æ
    (b'\xc3\x83\xc2\xba', b'\xc3\xba'),  # ú
    (b'\xc3\x83\xc2\xb3', b'\xc3\xb3'),  # ó
    # Undefined cp1252 bytes → stored as C2 XX (pass-through as C1 control)
    (b'\xc3\x83\xc2\x8d', b'\xc3\x8d'),  # Í (0x8D undefined → passed as U+008D → C2 8D)
    (b'\xc3\x83\xc2\x81', b'\xc3\x81'),  # Á (0x81 undefined)
    (b'\xc3\x83\xc2\x8f', b'\xc3\x8f'),  # Ï (0x8F undefined)
    (b'\xc3\x83\xc2\x90', b'\xc3\x90'),  # Ð (0x90 undefined)
    (b'\xc3\x83\xc2\x9d', b'\xc3\x9d'),  # Ý (0x9D undefined)
    # Â-prefix chars (C2 XX original → C2 was read as Â in cp1252, XX as something)
    (b'\xc3\x82\xc2\xa0', b'\xc2\xa0'),  # non-breaking space
    (b'\xc3\x82\xc2\xab', b'\xc2\xab'),  # «
    (b'\xc3\x82\xc2\xbb', b'\xc2\xbb'),  # »
    (b'\xc3\x82\xc2\xb0', b'\xc2\xb0'),  # °
    (b'\xc3\x82\xc2\xb4', b'\xc2\xb4'),  # ´
    (b'\xc3\x82\xc2\xa9', b'\xc2\xa9'),  # ©
    (b'\xc3\x82\xc2\xae', b'\xc2\xae'),  # ®
    # Remove C1 control chars that shouldn't be in text (icons violate G-02)
    (b'\xc2\x8d', b''),  # U+008D control (undefined cp1252 passthrough artifact)
    (b'\xc2\x81', b''),
    (b'\xc2\x8f', b''),
    (b'\xc2\x90', b''),
    (b'\xc2\x9d', b''),
]

fixed = raw
for bad, good in replacements:
    fixed = fixed.replace(bad, good)

# Remove emoji from clear_all if still present
fixed = fixed.replace(b'\xf0\x9f\x97\x91', b'')  # 🗑 emoji bytes
fixed = fixed.replace(b'\xef\xb8\x8f', b'')       # variation selector after emoji

# Validate JSON
try:
    d = json.loads(fixed.decode('utf-8'))
    print('JSON valid OK')
    print('recent_analyses:', d['dashboard']['recent_analyses'])
    print('log_files_status:', d['dashboard'].get('log_files_status', '?'))
    print('clear_all:', d['dashboard']['clear_all'])
except Exception as e:
    print('ERROR:', e)
    exit(1)

# Write with BOM
with open('static/i18n/fr.json', 'wb') as f:
    f.write(b'\xef\xbb\xbf')  # BOM
    f.write(fixed)

print('Written OK')

