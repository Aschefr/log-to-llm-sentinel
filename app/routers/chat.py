from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import asyncio

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, ChatConversation, ChatMessage
from app.services.orchestrator import Orchestrator

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
    
    # Si c'est une analyse manuelle (sans ID), on injecte le contexte comme premier message
    if raw_prompt and raw_response:
        # On peut tricher en mettant le contexte dans un message système caché ou juste le premier message
        # Ici on va l'injecter comme un message spécial ou juste le stocker
        # Pour l'instant, on va l'injecter comme message assistant initial
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
    
    # Récupérer l'analyse liée pour le contexte initial
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
    
    if not conv_id or not content:
        raise HTTPException(status_code=400, detail="Données manquantes")
        
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")

    # 1. Sauvegarder le message de l'utilisateur
    user_msg = ChatMessage(conversation_id=conv_id, role="user", content=content)
    db.add(user_msg)
    db.commit()

    # 2. Construire le contexte pour Ollama
    # On récupère toute l'histoire
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    
    prompt = ""
    if conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if analysis:
            rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
            cfg = db.query(GlobalConfig).first()
            base_prompt = _orchestrator._build_prompt(rule, analysis.triggered_line, cfg.system_prompt if cfg else "")
            prompt = f"{base_prompt}\n\nHistorique de la conversation :\n"
            # On ajoute la réponse initiale si elle n'est pas déjà dans les messages
            prompt += f"Assistant (initial) : {analysis.ollama_response}\n"

    for m in history:
        role_label = "Utilisateur" if m.role == "user" else "Assistant"
        prompt += f"{role_label} : {m.content}\n"
    
    prompt += "\nAssistant : "

    # 3. Appel Ollama
    cfg = db.query(GlobalConfig).first()
    async with _orchestrator._ollama_semaphore:
        response = await asyncio.to_thread(
            _orchestrator.ollama.analyze,
            prompt=prompt,
            url=cfg.ollama_url,
            model=cfg.ollama_model,
            timeout=120
        )

    # 4. Sauvegarder la réponse de l'IA
    ai_msg = ChatMessage(conversation_id=conv_id, role="assistant", content=response)
    db.add(ai_msg)
    db.commit()

    return {"status": "ok", "response": response}

@router.delete("/api/delete/{conv_id}")
async def delete_conversation(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    db.delete(conv)
    db.commit()
    return {"status": "ok"}
