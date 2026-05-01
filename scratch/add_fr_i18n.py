import json

with open('static/i18n/fr.json', 'r', encoding='utf-8-sig') as f:
    fr = json.load(f)

fr['config']['instance_name_label'] = "Nom de l'instance"
fr['config']['instance_name_placeholder'] = 'ex: Prod, Dev, Maison...'
fr['config']['instance_name_hint'] = "Ajouté en préfixe dans les sujets de notifications pour identifier cette instance lorsque plusieurs sont déployées."

with open('static/i18n/fr.json', 'w', encoding='utf-8-sig', newline='') as f:
    json.dump(fr, f, ensure_ascii=False, indent=2)
    f.write('\n')

print('FR OK')
