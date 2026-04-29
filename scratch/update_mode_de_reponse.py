import json

def update_lang(path, updates):
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    
    if "chat" not in data: data["chat"] = {}
    for k, v in updates.items():
        data["chat"][k] = v
        
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write('\n')

fr_updates = {
    "mode_de_reponse": "=== Contexte Automatique ===\nDate et heure actuelles : {datetime}\nLangue de réponse : {lang}\n\nLa plateforme de chat fait partie d'un environnement d'analyse de fichiers de log choisis par l'utilisateur.\nA titre informatif, voici les règles et fichiers surveillés :\n{rules_list}\n\n=== Mode de réponse (IMPORTANT) ===\nRéponds de manière directe, sans fioritures ni explications approfondies à moins que l'utilisateur ne le demande."
}

en_updates = {
    "mode_de_reponse": "=== Automatic Context ===\nCurrent date and time: {datetime}\nResponse language: {lang}\n\nThe chat platform is part of a log file analysis environment chosen by the user.\nFor your information, here are the monitored rules and files:\n{rules_list}\n\n=== Response Mode (IMPORTANT) ===\nAnswer directly, without fluff or deep explanations unless the user asks for it."
}

update_lang('static/i18n/fr.json', fr_updates)
update_lang('static/i18n/en.json', en_updates)
print("i18n updated.")
