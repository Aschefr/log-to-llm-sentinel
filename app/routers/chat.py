from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import asyncio
import json

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, ChatConversation, ChatMessage
from app.services.orchestrator import Orchestrator
from app.services.task_manager import task_manager, ChatTaskEntry
from app import logger

router = APIRouter(prefix="/chat", tags=["chat"])
templates = Jinja2Templates(directory="templates")

_orchestrator: Optional[Orchestrator] = None

def set_orchestrator(orch: Orchestrator):
    global _orchestrator
    _orchestrator = orch

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_class=HTMLResponse)
async def chat_page(request: Request, id: Optional[int] = None):
    return templates.TemplateResponse("chat.html", {"request": request, "conversation_id": id})

@router.get("/api/conversations")
async def list_conversations(db: Session = Depends(get_db)):
    convs = db.query(ChatConversation).order_by(ChatConversation.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "analysis_id": c.analysis_id
        } for c in convs
    ]

@router.post("/api/create")
async def create_conversation(data: dict, db: Session = Depends(get_db)):
    analysis_id = data.get("analysis_id")
    title = data.get("title", "Nouvelle conversation")
    raw_prompt = data.get("raw_context_prompt")
    raw_response = data.get("raw_context_response")
    
    if analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis:
            title = f"Analyse #{analysis.detection_id or analysis.id}"
    elif raw_prompt:
        title = "Analyse manuelle"
    
    conv = ChatConversation(title=title, analysis_id=analysis_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    
    if raw_prompt and raw_response:
        msg = ChatMessage(conversation_id=conv.id, role="assistant", content=f"**Contexte Manuel :**\n{raw_prompt}\n\n**Analyse initiale :**\n{raw_response}")
        db.add(msg)
        db.commit()
    
    return {"status": "ok", "id": conv.id}

@router.get("/api/history/{conv_id}")
async def get_history(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    messages = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    
    analysis = None
    if conv.analysis_id:
        a = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if a:
            analysis = {
                "triggered_line": a.triggered_line,
                "ollama_response": a.ollama_response,
                "detection_id": a.detection_id
            }
            
    return {
        "title": conv.title,
        "analysis": analysis,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in messages
        ]
    }

@router.post("/api/send")
async def send_message(data: dict, db: Session = Depends(get_db)):
    """
    Démarre la génération d'une réponse chat en arrière-plan.
    Retourne immédiatement un task_id. Le client se connecte ensuite
    à /api/stream/{task_id} pour lire les tokens au fur et à mesure.
    La génération continue même si le client se déconnecte.
    """
    conv_id = data.get("conversation_id")
    content = data.get("content")
    
    logger.info("ChatRouter", f"Requête reçue pour conv {conv_id}")
    
    if not conv_id or not content:
        raise HTTPException(status_code=400, detail="Données manquantes")
        
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")

    # 1. Sauvegarder le message utilisateur
    user_msg = ChatMessage(conversation_id=conv_id, role="user", content=content)
    db.add(user_msg)
    db.commit()

    # 2. Construire le contexte
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    
    prompt = ""
    if conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if analysis and _orchestrator:
            rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
            cfg = db.query(GlobalConfig).first()
            system_prompt = cfg.system_prompt.strip() if cfg and cfg.system_prompt else ""

            # On construit un prompt de CHAT — pas un prompt d'analyse.
            # Le but est de donner le contexte à l'IA pour qu'elle réponde
            # aux questions de l'utilisateur en tant qu'assistant, sans
            # ré-imposer le format SEVERITY ni les instructions d'analyse.
            context_block = "\n".join([
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
                "=== Mode de réponse (IMPORTANT) ===",
                "Tu participes à une CONVERSATION. L'analyse est déjà faite ci-dessus.",
                "Règles absolues :",
                "- Réponds à LA QUESTION posée, rien de plus",
                "- Pas de format SEVERITY, pas de liste 'd'étapes de correction' sauf si demandé explicitement",
                "- Si l'utilisateur dit que le service fonctionne, prends-le en compte dans ta réponse",
                "- Ton naturel et direct, comme dans un chat entre collègues experts",
                "- Si la question est courte, la réponse peut l'être aussi",
                "",
                "=== Conversation ===",
            ])
            prompt = context_block.strip() + "\n"

    for m in history[-10:]:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\n"
    prompt += "Assistant : "


    cfg = db.query(GlobalConfig).first()
    ollama_url    = (cfg.ollama_url    or "http://ollama:11434") if cfg else "http://ollama:11434"
    ollama_model  = (cfg.ollama_model  or "gemma4:e4b")         if cfg else "gemma4:e4b"
    ollama_temp   = cfg.ollama_temp   if cfg else 0.1
    ollama_ctx    = cfg.ollama_ctx    if cfg else 4096
    ollama_think  = cfg.ollama_think  if cfg else True

    # 3. Créer une tâche en arrière-plan
    entry = task_manager.create_chat_task(conv_id)
    task_id = entry.task_id

    # 4. Lancer la génération sans attendre (fire & forget)
    asyncio.create_task(
        _run_chat_generation(
            entry=entry,
            conv_id=conv_id,
            prompt=prompt,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            ollama_temp=ollama_temp,
            ollama_ctx=ollama_ctx,
            ollama_think=ollama_think,
        )
    )

    return {"status": "ok", "task_id": task_id}


async def _run_chat_generation(
    entry: ChatTaskEntry,
    conv_id: int,
    prompt: str,
    ollama_url: str,
    ollama_model: str,
    ollama_temp: float,
    ollama_ctx: int,
    ollama_think: bool,
):
    """
    Coroutine arrière-plan : génère les tokens Ollama, les accémule dans le buffer
    de l'entrée et sauvegarde la réponse complète en BDD à la fin.
    S'exécute indépendamment de la connexion HTTP du client.
    """
    full_response = ""
    is_thinking = False

    try:
        if not _orchestrator:
            entry.error = "Orchestrateur non initialisé"
            entry.status = "error"
            return

        async with _orchestrator._ollama_semaphore:
            async for chunk in _orchestrator.ollama.generate_stream(
                prompt=prompt,
                url=ollama_url,
                model=ollama_model,
                think=ollama_think,
                options={"temperature": ollama_temp, "num_ctx": ollama_ctx}
            ):
                if "error" in chunk:
                    entry.error = chunk["error"]
                    entry.status = "error"
                    return

                text = chunk.get("message", {}).get("content", "") or chunk.get("response", "")
                if text:
                    if not is_thinking:
                        if "<think>" in text:
                            is_thinking = True
                            to_send = text.split("<think>", 1)[0]
                            if to_send:
                                full_response += to_send
                                task_manager.append_chat_token(entry, to_send)
                        else:
                            full_response += text
                            task_manager.append_chat_token(entry, text)
                    else:
                        if "</think>" in text:
                            is_thinking = False
                            to_send = text.split("</think>", 1)[-1]
                            if to_send:
                                full_response += to_send
                                task_manager.append_chat_token(entry, to_send)

                if chunk.get("done"):
                    break

        # Sauvegarde BDD
        save_db = SessionLocal()
        try:
            ai_msg = ChatMessage(conversation_id=conv_id, role="assistant", content=full_response)
            save_db.add(ai_msg)
            save_db.commit()
            logger.info("ChatRouter", f"Réponse sauvegardée ({len(full_response)} car.)")
        finally:
            save_db.close()

        entry.status = "done"

    except Exception as e:
        logger.error("ChatRouter", f"Erreur génération arrière-plan : {str(e)}")
        entry.error = str(e)
        entry.status = "error"


@router.get("/api/stream/{task_id}")
async def stream_chat(task_id: str, from_pos: int = 0):
    """
    Endpoint SSE reconnectable : diffuse les tokens d'une tâche chat.
    Le paramètre `from_pos` permet de reprendre là où le client s'est arrêté.
    La connexion reste ouverte jusqu'à ce que la génération soit terminée.
    """
    entry = task_manager.get_chat_task(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Tâche non trouvée")

    async def generator():
        pos = from_pos
        while True:
            buf = entry.token_buffer
            if pos < len(buf):
                # Envoyer les tokens en attente (rattrapage ou nouveaux)
                for token in buf[pos:]:
                    pos += 1
                    yield f"data: {json.dumps({'text': token, 'pos': pos})}\n\n"
            elif entry.status == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            elif entry.status == "error":
                yield f"data: {json.dumps({'error': entry.error})}\n\n"
                break
            else:
                # Attendre de nouveaux tokens (polling léger)
                await task_manager.wait_for_token(entry, timeout=2.0)

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.get("/api/pending/{conv_id}")
async def get_pending_chat(conv_id: int):
    """
    Indique si une génération est en cours pour cette conversation.
    Utilisé par le frontend lors du retour sur l'onglet (visibilitychange).
    """
    entry = task_manager.get_pending_chat_for_conv(conv_id)
    if not entry:
        return {"task_id": None}
    return {
        "task_id": entry.task_id,
        "from_pos": len(entry.token_buffer),  # Le client reprend depuis la fin du buffer connu
        "buffered_tokens": len(entry.token_buffer),
    }

@router.delete("/api/delete/{conv_id}")
async def delete_conversation(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    db.delete(conv)
    db.commit()
    return {"status": "ok"}


@router.post("/api/regenerate/{conv_id}")
async def regenerate_last(conv_id: int, db: Session = Depends(get_db)):
    """
    Supprime le dernier message assistant de la conversation et retourne
    le contenu du dernier message utilisateur pour redéclencher la génération.
    Utilisé par le bouton 'Ré-essayer' sur les bulles IA.
    """
    # Supprimer le dernier message assistant
    last_assistant = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
        .first()
    )
    if last_assistant:
        db.delete(last_assistant)
        db.commit()

    # Retourner le dernier message utilisateur (pour le reré-envoyer)
    last_user = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .first()
    )
    if not last_user:
        raise HTTPException(status_code=404, detail="Aucun message utilisateur trouvé")

    return {"status": "ok", "last_user_content": last_user.content}


@router.delete("/api/messages/{conv_id}")
async def delete_last_messages(conv_id: int, count: int = 2, db: Session = Depends(get_db)):
    """
    Supprime les N derniers messages d'une conversation.
    Utilisé pour l'édition d'un message (supprime le message original
    et la réponse IA associée avant de renvoyer le message modifié).
    """
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(count)
        .all()
    )
    for m in messages:
        db.delete(m)
    db.commit()
    return {"status": "ok", "deleted": len(messages)}
