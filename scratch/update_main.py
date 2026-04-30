filepath = 'app/main.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace inactivity notification with i18n
old_inact = '''                                    subject = f"[Sentinel ALERTE] Inactivité détectée : {rule.name}"
                                    body = f"<p>Aucune ligne reçue sur la règle <b>{rule.name}</b> depuis plus de {rule.inactivity_period_hours} heures.</p><p>Dernière ligne : {rule.last_line_received_at.strftime('%Y-%m-%d %H:%M:%S')}</p>"'''

new_inact = '''                                    from app.utils.notification_i18n import nt
                                    lang = config.site_lang or 'fr'
                                    subject = nt('inactivity_subject', lang).format(rule_name=rule.name)
                                    body = nt('inactivity_body', lang).format(rule_name=rule.name, hours=rule.inactivity_period_hours, last_received=rule.last_line_received_at.strftime('%Y-%m-%d %H:%M:%S'))'''

content = content.replace(old_inact, new_inact, 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

assert "nt('inactivity_subject', lang)" in content, "inactivity_subject not found!"
print("main.py updated successfully!")
