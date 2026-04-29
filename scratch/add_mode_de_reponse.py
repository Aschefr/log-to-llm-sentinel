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
    "mode_de_reponse": "=== Mode de réponse (IMPORTANT) ===\nTu participes à une CONVERSATION. L'analyse est déjà faite ci-dessus.\nRègles absolues :\n- Réponds à LA QUESTION posée, rien de plus\n- Pas de format SEVERITY, pas de liste d'étapes de correction sauf si demandé explicitement\n- Si l'utilisateur dit que le service fonctionne, prends-le en compte dans ta réponse\n- Ton naturel et direct, comme dans un chat entre collègues experts\n- Si la question est courte, la réponse peut l'être aussi"
}

en_updates = {
    "mode_de_reponse": "=== Response Mode (IMPORTANT) ===\nYou are participating in a CONVERSATION. The analysis is already done above.\nAbsolute rules:\n- Answer THE QUESTION asked, nothing more\n- No SEVERITY format, no list of correction steps unless explicitly requested\n- If the user says the service is working, take it into account in your response\n- Natural and direct tone, like in a chat between expert colleagues\n- If the question is short, the answer can be too"
}

update_lang('static/i18n/fr.json', fr_updates)
update_lang('static/i18n/en.json', en_updates)
print("i18n updated.")
