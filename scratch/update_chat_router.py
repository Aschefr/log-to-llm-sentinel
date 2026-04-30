import os

CHAT_PY_PATH = "app/routers/chat.py"

with open(CHAT_PY_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update build_chat_prompt
# We want to replace the logic that adds the analysis block and history.
# Wait, actually we can just find:
# if conv.analysis_id:
# and change it to:
# if conv.analysis_id and not conv.compressed_at:
#
# And for history:
# for m in history[-10:]:
# We change to:
# for m in history:
#     if conv.compressed_at and m.created_at <= conv.compressed_at: continue
#     ... and then slice to last 10 ?
# Or better, filter in the query!
# Actually, I'll rewrite the entire build_chat_prompt function cleanly.

new_build_chat_prompt = """def build_chat_prompt(conv_id: int, db: Session) -> tuple[str, int]:
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        return "", 4096
        
    history_query = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id)
    if conv.compressed_at:
        history_query = history_query.filter(ChatMessage.created_at > conv.compressed_at)
    history = history_query.order_by(ChatMessage.created_at.asc()).all()
    
    # 1. Charger la configuration de base (System Prompt & Mode Text)
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
    
    if mode_text:
        mode_text = mode_text.replace("{datetime}", current_dt)
        mode_text = mode_text.replace("{lang}", lang.upper())
        mode_text = mode_text.replace("{rules_list}", rules_list_str)

    # 2. Construire le bloc de contexte système
    block_lines = []
    if system_prompt:
        block_lines.extend([system_prompt, ""])
        
    if conv.compressed_context:
        block_lines.extend([
            "=== Contexte Compressé ===",
            conv.compressed_context,
            ""
        ])
    elif conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if analysis:
            rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
            block_lines.extend([
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
                ""
            ])

    if mode_text:
        block_lines.append(mode_text)

    prompt = "\\n".join(block_lines).strip() + "\\n\\n" if block_lines else ""

    # 3. Ajouter l'historique de la conversation
    # On limite à 20 messages récents au lieu de 10 car le contexte est géré
    for m in history[-20:]:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\\n"
    
    ollama_ctx = cfg.ollama_ctx if cfg else 4096
    
    return prompt, ollama_ctx"""

import re
content = re.sub(r'def build_chat_prompt\(conv_id: int, db: Session\) -> tuple\[str, int\]:.*?return prompt, ollama_ctx', new_build_chat_prompt, content, flags=re.DOTALL)

# 2. Add API endpoints for compression
# We will insert them right after `get_chat_context`
endpoints = """
import uuid
from pydantic import BaseModel
from app.utils.compression import run_compaction, run_summary

_compression_tasks = {}

class CompressRequest(BaseModel):
    mode: str

@router.post("/api/compress/{conv_id}")
async def start_compression(conv_id: int, req: CompressRequest, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    # Build full prompt WITHOUT system/mode rules (just analysis + history)
    # Actually, we can just use build_chat_prompt but strip system rules?
    # No, let's just get the history up to now.
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    text_to_compress = ""
    
    if conv.analysis_id and not conv.compressed_at:
        a = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if a:
            text_to_compress += f"=== Analyse initiale ===\\n{a.triggered_line}\\n{a.ollama_response}\\n\\n"
            
    for m in history:
        if conv.compressed_at and m.created_at <= conv.compressed_at:
            continue
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        text_to_compress += f"{role_label} : {m.content}\\n\\n"
        
    text_to_compress = text_to_compress.strip()
    cutoff_time = datetime.utcnow()
    
    if req.mode == "compact":
        compressed = run_compaction(text_to_compress)
        conv.compression_mode = "compact"
        conv.compressed_context = compressed
        conv.compressed_at = cutoff_time
        db.commit()
        return {"status": "done", "mode": "compact"}
        
    elif req.mode == "summary":
        task_id = str(uuid.uuid4())
        _compression_tasks[task_id] = {"status": "running", "result": None, "error": None}
        
        async def background_summary(t_id, txt, cid, dt):
            try:
                if not _orchestrator:
                    raise Exception("Orchestrator non initialisé")
                res = await run_summary(txt, _orchestrator)
                _compression_tasks[t_id]["status"] = "done"
                _compression_tasks[t_id]["result"] = res
                
                # Save to DB
                with SessionLocal() as s:
                    c = s.query(ChatConversation).filter(ChatConversation.id == cid).first()
                    if c:
                        c.compression_mode = "summary"
                        c.compressed_context = res
                        c.compressed_at = dt
                        s.commit()
            except Exception as e:
                _compression_tasks[t_id]["status"] = "error"
                _compression_tasks[t_id]["error"] = str(e)
                
        asyncio.create_task(background_summary(task_id, text_to_compress, conv_id, cutoff_time))
        return {"status": "running", "task_id": task_id}
        
    else:
        raise HTTPException(status_code=400, detail="Mode invalide")

@router.get("/api/compress/status/{task_id}")
async def get_compression_status(task_id: str):
    if task_id not in _compression_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return _compression_tasks[task_id]

@router.delete("/api/compress/{conv_id}")
async def revert_compression(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv.compression_mode = None
    conv.compressed_context = None
    conv.compressed_at = None
    db.commit()
    return {"status": "ok"}

@router.put("/api/compress/{conv_id}")
async def update_compression(conv_id: int, data: dict, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    new_context = data.get("compressed_context")
    if not new_context:
        raise HTTPException(status_code=400, detail="compressed_context requis")
    conv.compressed_context = new_context
    db.commit()
    return {"status": "ok"}
"""

content = content.replace('@router.get("/api/context/{conv_id}")', endpoints + '\n@router.get("/api/context/{conv_id}")')

# Also update /api/history to return compressed_context and compressed_at
# Find:
#     return {
#         "title": conv.title,
#         "analysis_id": conv.analysis_id,
#         "analysis": analysis,
#         "messages": [
#             {

# Change to:
#     return {
#         "title": conv.title,
#         "analysis_id": conv.analysis_id,
#         "analysis": analysis,
#         "compression_mode": conv.compression_mode,
#         "compressed_context": conv.compressed_context,
#         "compressed_at": conv.compressed_at.isoformat() if conv.compressed_at else None,
#         "messages": [

hist_ret = """    return {
        "title": conv.title,
        "analysis_id": conv.analysis_id,
        "analysis": analysis,"""
hist_ret_new = """    return {
        "title": conv.title,
        "analysis_id": conv.analysis_id,
        "analysis": analysis,
        "compression_mode": conv.compression_mode,
        "compressed_context": conv.compressed_context,
        "compressed_at": conv.compressed_at.isoformat() if conv.compressed_at else None,"""
content = content.replace(hist_ret, hist_ret_new)

with open(CHAT_PY_PATH, "w", encoding="utf-8") as f:
    f.write(content)
print("chat.py updated successfully!")
