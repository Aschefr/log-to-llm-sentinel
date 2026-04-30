import os
import re

CHAT_PY_PATH = "app/routers/chat.py"

with open(CHAT_PY_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# 1. We'll add the new function `build_chat_prompt` right before `send_message`.
# Let's find `def send_message`
send_msg_idx = content.find("@router.post(\"/api/send\")")

new_func = """
def build_chat_prompt(conv_id: int, db: Session) -> tuple[str, int]:
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        return "", 4096
        
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    prompt = ""
    if conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if analysis and _orchestrator:
            rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
            cfg = db.query(GlobalConfig).first()
            system_prompt = cfg.chat_system_prompt.strip() if cfg and cfg.chat_system_prompt else ""
            lang = cfg.chat_lang if cfg and cfg.chat_lang else "fr"
            lang_file = f"static/i18n/{lang}.json"
            mode_text = ""
            import os, json
            from datetime import datetime
            if os.path.exists(lang_file):
                with open(lang_file, 'r', encoding='utf-8-sig') as f:
                    lang_data = json.load(f)
                    mode_text = lang_data.get("chat", {}).get("mode_de_reponse", "")

            current_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            active_rules = db.query(Rule).filter(Rule.enabled == True).all()
            rules_lines = []
            for r in active_rules:
                ctx = f" (Context: {r.application_context})" if r.application_context else ""
                rules_lines.append(f"- {r.name}{ctx}")
            rules_list_str = "\\n".join(rules_lines) if rules_lines else "- Aucune règle configurée"
            
            mode_text = mode_text.replace("{datetime}", current_dt)
            mode_text = mode_text.replace("{lang}", lang.upper())
            mode_text = mode_text.replace("{rules_list}", rules_list_str)

            context_block = "\\n".join([
                system_prompt if system_prompt else "",
                "",
                "Tu es un assistant expert en systèmes Linux, Docker et infrastructure.",
                "Une analyse de log a été effectuée. L'utilisateur te pose des questions de suivi.",
                "",
                f"=== Règle : {rule.name if rule else 'Inconnue'} ===",
                f"Application : {rule.application_context if rule else ''}",
                "",
                "=== Ligne de log concernée ===",
                analysis.triggered_line,
                "",
                "=== Analyse initiale ===",
                analysis.ollama_response,
                "",
                mode_text
            ])
            prompt = context_block.strip() + "\\n"

    for m in history[-10:]:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\\n"
    
    cfg = db.query(GlobalConfig).first()
    ollama_ctx = cfg.ollama_ctx if cfg else 4096
    
    return prompt, ollama_ctx

@router.get("/api/context/{conv_id}")
async def get_chat_context(conv_id: int, db: Session = Depends(get_db)):
    base_prompt, ollama_ctx = build_chat_prompt(conv_id, db)
    return {"base_prompt": base_prompt, "ollama_ctx": ollama_ctx}

"""

# 2. We replace the large block in `send_message` with a call to `build_chat_prompt`.
# The block to replace is:
'''
    # 2. Construire le contexte
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    
    prompt = ""
    if conv.analysis_id:
'''
# up to
'''
    for m in history[-10:]:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\\n"
    prompt += "Assistant : "
'''

# We will use regex or find to isolate it.

start_block = "    # 2. Construire le contexte"
end_block = "    prompt += \"Assistant : \"\n"

s_idx = content.find(start_block)
e_idx = content.find(end_block) + len(end_block)

if s_idx != -1 and e_idx != -1:
    content = content[:send_msg_idx] + new_func + content[send_msg_idx:s_idx] + """    # 2. Construire le contexte
    prompt, ollama_ctx = build_chat_prompt(conv_id, db)
    prompt += "Assistant : "
""" + content[e_idx:]
    
    with open(CHAT_PY_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("chat.py updated successfully.")
else:
    print("Could not find the block to replace!")
