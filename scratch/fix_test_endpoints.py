filepath = 'app/routers/config.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add import for nt
content = content.replace(
    'from app import logger\n',
    'from app import logger\nfrom app.utils.notification_i18n import nt\n',
    1
)

# 2. Fix test SMTP
old_smtp_test = '''        notifier = NotificationService()
        subject = "[Log to LLM Sentinel] Test SMTP"
        body = "<p>Ceci est un email de test envoyé par Log-to-LLM-Sentinel.</p>"'''

new_smtp_test = '''        notifier = NotificationService()
        lang = cfg.get("site_lang", "fr")
        subject = "[Log to LLM Sentinel] Test SMTP"
        body = "<p>This is a test email sent by Log-to-LLM-Sentinel.</p>" if lang == "en" else "<p>Ceci est un email de test envoyé par Log-to-LLM-Sentinel.</p>"'''

content = content.replace(old_smtp_test, new_smtp_test, 1)

# 3. Fix test Apprise
old_apprise_test = '''        subject = "Test Apprise Log to LLM Sentinel"
        body = "Ceci est un test de configuration Log to LLM Sentinel"'''

new_apprise_test = '''        lang = cfg.get("site_lang", "fr")
        subject = "Test Apprise Log to LLM Sentinel"
        body = "This is a Log to LLM Sentinel configuration test" if lang == "en" else "Ceci est un test de configuration Log to LLM Sentinel"'''

content = content.replace(old_apprise_test, new_apprise_test, 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

assert '"site_lang", "fr"' in content
print("config.py test endpoints updated!")
