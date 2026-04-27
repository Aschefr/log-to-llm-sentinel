with open('static/js/rules.js', 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    ("c.classList.remove('active');", "c.classList.remove('kw-tab--active');"),
    ("card.classList.add('active');", "card.classList.add('kw-tab--active');"),
    ("localCard.classList.add('active');", "localCard.classList.add('kw-tab--active');"),
    ("document.querySelector('.source-card.active')", "document.querySelector('.source-card.kw-tab--active')"),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"Replaced: {old[:50]}")
    else:
        print(f"NOT FOUND: {old[:50]}")

with open('static/js/rules.js', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
