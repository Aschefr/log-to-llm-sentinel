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
    conv_id = data.get("conversation_id")
    content = data.get("content")
    
    logger.info("ChatRouter", f"Requête reçue pour conv {conv_id}")
    
    if not conv_id or not content:
        raise HTTPException(status_code=400, detail="Données manquantes")
        
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")

    # 1. Sauvegarder le message de l'utilisateur
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
            base_prompt = _orchestrator._build_prompt(rule, analysis.triggered_line, cfg.system_prompt if cfg else "")
            prompt = f"{base_prompt}\n\nHistorique :\nAssistant (initial) : {analysis.ollama_response}\n"

    for m in history:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\n"
    
    prompt += "\nAssistant : "

    cfg = db.query(GlobalConfig).first()
    ollama_url = (cfg.ollama_url or "http://ollama:11434") if cfg else "http://ollama:11434"
    ollama_model = (cfg.ollama_model or "gemma4:e4b") if cfg else "gemma4:e4b"

    async def event_generator():
        full_response = ""
        logger.info("ChatRouter", f"Démarrage event_generator pour conv {conv_id}")
        try:
            if not _orchestrator:
                yield f"data: {json.dumps({'error': 'Orchestrateur non initialisé'})}\n\n"
                return

            async with _orchestrator._ollama_semaphore:
                logger.debug("ChatRouter", "Sémophore acquis")
                async for chunk in _orchestrator.ollama.generate_stream(
                    prompt=prompt,
                    url=ollama_url,
                    model=ollama_model
                ):
                    if "error" in chunk:
                        yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                        return

                    if "response" in chunk:
                        text = chunk["response"]
                        full_response += text
                        yield f"data: {json.dumps({'text': text})}\n\n"
                    
                    if chunk.get("done"):
                        break
            
            # Sauvegarde BDD
            new_db = SessionLocal()
            try:
                ai_msg = ChatMessage(conversation_id=conv_id, role="assistant", content=full_response)
                new_db.add(ai_msg)
                new_db.commit()
                logger.info("ChatRouter", f"Réponse sauvegardée ({len(full_response)} car.)")
            finally:
                new_db.close()
                
        except Exception as e:
            logger.error("ChatRouter", f"Erreur dans event_generator : {str(e)}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.delete("/api/delete/{conv_id}")
async def delete_conversation(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    db.delete(conv)
    db.commit()
    return {"status": "ok"}
