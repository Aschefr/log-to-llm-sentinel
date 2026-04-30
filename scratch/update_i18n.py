filepath = 'static/js/i18n.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Add backend sync after localStorage.setItem
old_set = "localStorage.setItem('sentinel_lang', lang);"
new_set = """localStorage.setItem('sentinel_lang', lang);
            // Sync site language to backend for notifications
            fetch('/api/config/site-lang', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lang: lang })
            }).catch(e => console.warn('[i18n] Failed to sync site language:', e));"""

content = content.replace(old_set, new_set, 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

assert '/api/config/site-lang' in content, "sync not found!"
print("i18n.js updated successfully!")
