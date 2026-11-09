from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import asyncio
import json

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, ChatConversation, ChatMessage, ChatCompression, ChatAction
from app.services.app_config import config_to_dict, llm_options
from app.services.chat_agent import run_agent_turn, replay_tool_messages, AgentLimits, AgentResult
from app.services.llm_tools import apply_action, action_to_dict
from app.services import cve_lookup, web_search
from app.services.prompts import SECURITY_ALERT_INSTRUCTIONS, WEB_TOOLS_INSTRUCTIONS
from app.services.prompts import CHAT_ANALYSIS_CONTEXT, CHAT_TOOLS_INSTRUCTIONS, DIAG_TOOLS_INSTRUCTIONS, DIAG_LINKS_INSTRUCTIONS, INVESTIGATION_PROMPT
from app.services import dashboard_watch, diagnostics, event_notes
from app.services.alert_analysis import render_notes
from app.services.orchestrator import Orchestrator
from app.services.task_manager import task_manager, ChatTaskEntry
from app import logger

router = APIRouter(prefix="/chat", tags=["chat"])
templates = Jinja2Templates(directory="templates")

# Share the same globals as main.py templates (needed because base.html uses them)
from app.main import APP_VERSION, check_for_updates
templates.env.globals['APP_VERSION'] = APP_VERSION
templates.env.globals['UPDATE_STATUS'] = check_for_updates(APP_VERSION)

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
    """Conversations, de la plus récemment active à la plus ancienne."""
    convs = db.query(ChatConversation).all()
    last_at = dict(db.query(ChatMessage.conversation_id, func.max(ChatMessage.created_at))
                   .group_by(ChatMessage.conversation_id).all())
    pending = dict(db.query(ChatAction.conversation_id, func.count(ChatAction.id))
                   .filter(ChatAction.status == "pending").group_by(ChatAction.conversation_id).all())
    linked = {a.id: a for a in db.query(Analysis).filter(
        Analysis.id.in_([c.analysis_id for c in convs if c.analysis_id])).all()}
    rule_names = dict(db.query(Rule.id, Rule.name).all())

    def row(c):
        analysis = linked.get(c.analysis_id)
        active = last_at.get(c.id) or c.created_at
        return {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "last_at": active.isoformat(),
            "analysis_id": c.analysis_id,
            "severity": analysis.severity if analysis else None,
            "rule_name": rule_names.get(analysis.rule_id) if analysis else None,
            "pending_actions": pending.get(c.id, 0),
            "running": task_manager.get_pending_chat_for_conv(c.id) is not None,
        }
    return sorted((row(c) for c in convs), key=lambda r: r["last_at"], reverse=True)


@router.get("/api/status")
async def chat_status(db: Session = Depends(get_db)):
    """Ce que l'IA du chat peut faire à cet instant. `tools` vaut None quand Ollama ne permet pas de le savoir."""
    config = config_to_dict(db.query(GlobalConfig).first())
    ollama = await asyncio.to_thread(dashboard_watch.ollama_status, config, _orchestrator)
    tools = None
    if ollama["state"] == "ok" and _orchestrator:
        tools = "tools" in await _orchestrator.ollama.get_capabilities(config["ollama_url"], config["ollama_model"])
    actions = db.query(ChatAction.id, ChatAction.conversation_id).filter(ChatAction.status == "pending") \
        .order_by(ChatAction.id.asc()).all()
    return {
        "ollama": ollama,
        "tools": tools,
        "diagnostics": diagnostics.availability(),
        "web_search": web_search.status(),
        "pending_actions": [{"id": a.id, "conversation_id": a.conversation_id} for a in actions],
        "compression_mode": _compression_mode(db),
    }

@router.post("/api/create")
async def create_conversation(data: dict, db: Session = Depends(get_db)):
    analysis_id = data.get("analysis_id")
    en = _chat_lang(db.query(GlobalConfig).first()) == "en"
    title = data.get("title") or ("New conversation" if en else "Nouvelle conversation")
    raw_prompt = data.get("raw_context_prompt")
    raw_response = data.get("raw_context_response")

    if analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis:
            title = f"{'Analysis' if en else 'Analyse'} #{analysis.detection_id or analysis.id}"
    elif raw_prompt:
        title = "Manual analysis" if en else "Analyse manuelle"
    
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
            rule = db.query(Rule).filter(Rule.id == a.rule_id).first()
            analysis = {
                "id": a.id,
                "triggered_line": a.triggered_line,
                "ollama_response": a.ollama_response,
                "detection_id": a.detection_id,
                "severity": a.severity,
                "analyzed_at": a.analyzed_at.isoformat() if a.analyzed_at else None,
                "rule_id": a.rule_id,
                "rule_name": rule.name if rule else None,
            }

    # Trace des outils (sorties comprises, pour vérifier la réponse) et actions proposées, pour l'affichage
    lang = _chat_lang(db.query(GlobalConfig).first())
    def _meta(m):
        if not m.metadata_json:
            return None
        meta = json.loads(m.metadata_json)
        return {"tools": [{"label": t.get("label") or t["name"], "error": str(t.get("result", "")).startswith("Erreur"),
                           "output": str(t.get("result", "")), "lines": len(str(t.get("result", "")).splitlines())}
                          for t in meta.get("tools") or []],
                "actions": meta.get("actions") or [],
                "kind": meta.get("kind")}
    actions = {a.id: action_to_dict(a, lang)
               for a in db.query(ChatAction).filter(ChatAction.conversation_id == conv_id).all()}
            
    # 09-B : récupérer l'historique des compressions
    compressions = db.query(ChatCompression).filter(
        ChatCompression.conversation_id == conv_id
    ).order_by(ChatCompression.compressed_at.asc()).all()

    return {
        "title": conv.title,
        "analysis_id": conv.analysis_id,
        "analysis": analysis,
        "compression_mode": conv.compression_mode,
        "compressed_context": conv.compressed_context,
        "compressed_at": conv.compressed_at.isoformat() if conv.compressed_at else None,
        "compressions": [
            {
                "id": c.id,
                "mode": c.mode,
                "content": c.content,
                "compressed_at": c.compressed_at.isoformat() if c.compressed_at else None
            }
            for c in compressions
        ],
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(), "meta": _meta(m)}
            for m in messages
        ],
        "actions": actions,
    }


def _chat_lang(cfg) -> str:
    return cfg.site_lang if cfg and cfg.site_lang else "fr"


def _compression_mode(db: Session) -> str:
    cfg = db.query(GlobalConfig).first()
    return (cfg.chat_compression_mode or "") if cfg else ""


def _analysis_block(analysis: Analysis, rule: Optional[Rule], lang: str) -> list:
    """Contexte complet de l'analyse liée à la conversation (événements, contexte de la source, analyse)."""
    en = lang == "en"
    lines = [CHAT_ANALYSIS_CONTEXT.get(lang, CHAT_ANALYSIS_CONTEXT["fr"]), ""]
    if rule:
        lines.append(f"=== {'Rule' if en else 'Règle'} #{rule.id} : {rule.name} ===")
        if rule.application_context:
            lines.append(f"{'Application' if en else 'Application'} : {rule.application_context}")
    lines.append(f"{'Analysis' if en else 'Analyse'} #{analysis.id} [{analysis.detection_id or '-'}] — "
                 f"{analysis.analyzed_at.strftime('%Y-%m-%d %H:%M') if analysis.analyzed_at else '?'} UTC — {analysis.severity}")
    lines += ["", f"=== {'Triggering events' if en else 'Événements déclencheurs'} ===", analysis.triggered_line or ""]
    before = json.loads(analysis.context_before_json or "[]")
    after = json.loads(analysis.context_after_json or "[]")
    if before or after:
        lines += ["", f"=== {'Context in the source' if en else 'Contexte dans la source'} ===", *before,
                  f"[… {'triggering events above' if en else 'événements déclencheurs ci-dessus'} …]", *after]
    lines += ["", f"=== {'Initial analysis' if en else 'Analyse initiale'} ===", analysis.ollama_response or "", ""]
    return lines


def build_chat_messages(conv_id: int, db: Session, with_tools: bool = False) -> tuple[list, int]:
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        return [], 4096

    history_query = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id)
    if conv.compressed_at:
        history_query = history_query.filter(ChatMessage.created_at > conv.compressed_at)
    history = history_query.order_by(ChatMessage.created_at.asc()).all()

    # 1. Charger la configuration de base (System Prompt & Mode Text)
    cfg = db.query(GlobalConfig).first()
    system_prompt = cfg.chat_system_prompt.strip() if cfg and cfg.chat_system_prompt else ""
    lang = _chat_lang(cfg)
    lang_file = f"static/i18n/{lang}.json"
    mode_text = ""

    import os
    if os.path.exists(lang_file):
        with open(lang_file, 'r', encoding='utf-8-sig') as f:
            lang_data = json.load(f)
            mode_text = lang_data.get("chat", {}).get("mode_de_reponse", "")

    current_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    active_rules = db.query(Rule).filter(Rule.enabled == True).all()
    rules_lines = []
    for r in active_rules:
        ctx = f" (Context: {r.application_context})" if r.application_context else ""
        status = f" — {r.alert_status}" if r.alert_status and r.alert_status != "normal" else ""
        rules_lines.append(f"- #{r.id} {r.name}{ctx}{status}")
    rules_list_str = "\n".join(rules_lines) if rules_lines else "- Aucune règle configurée"

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
            block_lines.extend(_analysis_block(analysis, rule, lang))

    # Hors du bloc ci-dessus : les notes restent connues une fois la conversation compressée
    if conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        notes_text = render_notes(event_notes.for_analysis(db, analysis), lang) if analysis else ""
        if notes_text:
            block_lines.extend([notes_text, ""])

    if mode_text:
        block_lines.append(mode_text)
    if with_tools:
        block_lines.extend(["", CHAT_TOOLS_INSTRUCTIONS.get(lang, CHAT_TOOLS_INSTRUCTIONS["fr"])])
        links = diagnostics.links_for_conversation(db, conv_id)
        kinds = {link["kind"] for link in links}
        web_available = "web" in kinds
        links, kinds = [l for l in links if l["kind"] != "web"], kinds - {"web"}
        diag_tools = (["docker_list_containers", "docker_container_status", "docker_container_logs", "docker_container_stats"]
                      if "docker" in kinds else []) + (["host_check"] if "ssh" in kinds else [])
        if diag_tools:
            text = DIAG_TOOLS_INSTRUCTIONS.get(lang, DIAG_TOOLS_INSTRUCTIONS["fr"]).replace("{tools}", ", ".join(diag_tools))
            block_lines.extend(["", text])
            if any(sum(l["kind"] == kind for l in links) > 1 for kind in kinds):
                listing = "\n".join(f"- {l['name']} ({'Docker' if l['kind'] == 'docker' else 'SSH'})" for l in links)
                block_lines.append(DIAG_LINKS_INSTRUCTIONS.get(lang, DIAG_LINKS_INSTRUCTIONS["fr"]).replace("{links}", listing))
        external = (["lookup_cve"] if cve_lookup.enabled(db) else []) + (["web_search"] if web_available else [])
        if external:
            block_lines.extend(["", WEB_TOOLS_INSTRUCTIONS.get(lang, WEB_TOOLS_INSTRUCTIONS["fr"]).replace("{tools}", ", ".join(external))])
        cited = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first() if conv.analysis_id else None
        if cited and cve_lookup.first_cve(cited.triggered_line):
            block_lines.extend(["", SECURITY_ALERT_INSTRUCTIONS.get(lang, SECURITY_ALERT_INSTRUCTIONS["fr"])])

    system = "\n".join(block_lines).strip()
    messages = [{"role": "system", "content": system}] if system else []

    # 3. Ajouter l'historique de la conversation (vrais rôles user / assistant)
    # On limite à 20 messages récents au lieu de 10 car le contexte est géré.
    # Les résultats d'outils ne sont rejoués que pour les 2 derniers tours qui en ont utilisé (budget de contexte).
    recent = history[-20:]
    with_trace = [m.id for m in recent if m.role == "assistant" and m.metadata_json][-2:]
    for m in recent:
        if m.id in with_trace:
            messages += replay_tool_messages(json.loads(m.metadata_json).get("tools") or [])
        messages.append({"role": "assistant" if m.role == "assistant" else "user", "content": m.content})

    ollama_ctx = cfg.ollama_ctx if cfg else 4096

    return messages, ollama_ctx


def build_chat_prompt(conv_id: int, db: Session) -> tuple[str, int]:
    """Représentation texte des messages du chat (affichage et estimation du contexte côté interface)."""
    messages, ollama_ctx = build_chat_messages(conv_id, db, with_tools=True)
    prompt = ""
    for m in messages:
        if m["role"] == "system":
            prompt += m["content"] + "\n\n"
        elif m["role"] == "tool":
            prompt += f"[{m.get('tool_name', 'tool')}] {m['content']}\n"
        elif m.get("content"):
            role_label = "Utilisateur" if m["role"] == "user" else "Assistant"
            prompt += f"{role_label} : {m['content']}\n"
    return prompt, ollama_ctx


import uuid
from pydantic import BaseModel
from app.utils.compression import SummaryRejected, message_line, run_anchored, run_truncation, split_kept

_compression_tasks = {}

class CompressRequest(BaseModel):
    mode: str

@router.post("/api/compress/{conv_id}")
async def start_compression(conv_id: int, req: CompressRequest, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    history = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at.asc()).all()
    
    # Filtrer les messages déjà compressés
    messages_to_process = []
    for m in history:
        if conv.compressed_at and m.created_at <= conv.compressed_at:
            continue
        messages_to_process.append(m)
    
    if not messages_to_process:
        raise HTTPException(status_code=400, detail="Aucun message à compresser")
    if req.mode == "compact":
        req.mode = "summary"  # ancien mode « compacter », remplacé par le résumé ancré (2026-11-08)

    if req.mode == "truncate":
        # Truncate : garder les récents, résumer les anciens en une ligne chacun
        cfg = db.query(GlobalConfig).first()
        max_ctx = cfg.ollama_ctx if cfg else 4096
        
        dropped, kept = run_truncation(messages_to_process, max_ctx)
        
        if not dropped:
            return {"status": "done", "mode": "truncate", "message": "Rien à tronquer"}
        
        # Construire le résumé des messages tronqués
        dropped_lines = []
        for m in dropped:
            role_label = "U" if m.role == "user" else "A"
            first_line = m.content.strip().split('\n')[0][:120]
            dropped_lines.append(f"- {role_label}: {first_line}")
        
        compressed = "[Tronqué]\n" + "\n".join(dropped_lines)
        
        # Cutoff = timestamp du premier message GARDÉ (les kept restent visibles)
        cutoff_time = kept[0].created_at if kept else datetime.utcnow()
        # Reculer d'1 seconde pour que le premier kept passe le filtre ">"
        from datetime import timedelta
        cutoff_time = cutoff_time - timedelta(seconds=1)
        
        conv.compression_mode = "truncate"
        conv.compressed_context = compressed
        conv.compressed_at = cutoff_time
        # 09-B : enregistrer dans l'historique des compressions
        new_comp = ChatCompression(
            conversation_id=conv_id,
            mode="truncate",
            content=compressed,
            compressed_at=cutoff_time
        )
        db.add(new_comp)
        db.commit()
        
        return {"status": "done", "mode": "truncate"}
    
    # Résumé ancré : les derniers échanges restent mot pour mot, le reste est fusionné dans le résumé précédent
    if req.mode != "summary":
        raise HTTPException(status_code=400, detail="Mode invalide")
    cfg = db.query(GlobalConfig).first()
    url = (cfg.ollama_url if cfg else None) or "http://ollama:11434"
    model = (cfg.ollama_model if cfg else None) or "gemma4:e4b"
    ctx_size = (cfg.ollama_ctx if cfg else None) or 32768
    to_summarize, _kept = split_kept(messages_to_process, ctx_size)
    if not to_summarize:
        return {"status": "done", "mode": "summary", "message": "Rien à résumer : seuls les derniers échanges restent"}
    previous = conv.compressed_context or ""
    lines = []
    if not previous and conv.analysis_id:
        # Première compaction : l'analyse initiale quitte le contexte avec les messages, elle entre donc dans le résumé
        a = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if a:
            lines.append(f"=== Analyse initiale ===\n{a.triggered_line}\n{a.ollama_response}")
    lines += [message_line(m.role, m.content) for m in to_summarize]
    lang = _chat_lang(cfg)
    cutoff_time = to_summarize[-1].created_at  # les échanges gardés, plus récents, restent lus tels quels

    task_id = str(uuid.uuid4())
    _compression_tasks[task_id] = {"status": "running", "result": None, "error": None}

    async def background_compress(t_id, cid, dt):
        try:
            if not _orchestrator:
                raise Exception("Orchestrator non initialisé")
            res = await run_anchored(previous, lines, _orchestrator.ollama, url, model, num_ctx=ctx_size, lang=lang)
            if not res:
                raise Exception("Le modèle a rendu un résumé vide")
            with SessionLocal() as s:
                c = s.query(ChatConversation).filter(ChatConversation.id == cid).first()
                if c:
                    c.compression_mode = "summary"
                    c.compressed_context = res
                    c.compressed_at = dt
                    s.add(ChatCompression(conversation_id=cid, mode="summary", content=res, compressed_at=dt))
                    s.commit()
            _compression_tasks[t_id]["status"] = "done"
            _compression_tasks[t_id]["result"] = res
        except SummaryRejected:
            _compression_tasks[t_id]["status"] = "error"
            _compression_tasks[t_id]["error"] = "summary_rejected"  # traduit par l'interface
        except Exception as e:
            _compression_tasks[t_id]["status"] = "error"
            _compression_tasks[t_id]["error"] = str(e)

    asyncio.create_task(background_compress(task_id, conv_id, cutoff_time))
    return {"status": "running", "task_id": task_id}

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
    # 09-B : supprimer tout l'historique des compressions de cette conv
    db.query(ChatCompression).filter(ChatCompression.conversation_id == conv_id).delete()
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
    # Mettre à jour conv + dernière entrée ChatCompression (09-B)
    conv.compressed_context = new_context
    last_comp = db.query(ChatCompression).filter(
        ChatCompression.conversation_id == conv_id
    ).order_by(ChatCompression.compressed_at.desc()).first()
    if last_comp:
        last_comp.content = new_context
    db.commit()
    return {"status": "ok"}

@router.delete("/api/message/{msg_id}")
async def delete_message(msg_id: int, db: Session = Depends(get_db)):
    """Supprime un message individuel par son ID."""
    msg = db.query(ChatMessage).filter(ChatMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(msg)
    db.commit()
    return {"status": "ok"}

@router.get("/api/context/{conv_id}")
async def get_chat_context(conv_id: int, db: Session = Depends(get_db)):
    base_prompt, ollama_ctx = build_chat_prompt(conv_id, db)
    return {"base_prompt": base_prompt, "ollama_ctx": ollama_ctx}

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
        raise HTTPException(status_code=400, detail="missing_data")

    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")

    # 1. Sauvegarder le message utilisateur
    user_msg = ChatMessage(conversation_id=conv_id, role="user", content=content)
    db.add(user_msg)
    db.commit()

    task_id = await _start_generation(conv_id, db)
    return {"status": "ok", "task_id": task_id}


async def _start_generation(conv_id: int, db: Session, limits: AgentLimits = AgentLimits()) -> str:
    """Construit le contexte et lance la génération (avec outils si le modèle les supporte) en arrière-plan."""
    config = config_to_dict(db.query(GlobalConfig).first())
    use_tools = False
    if _orchestrator:
        caps = await _orchestrator.ollama.get_capabilities(config["ollama_url"], config["ollama_model"])
        use_tools = "tools" in caps
    messages, _ = build_chat_messages(conv_id, db, with_tools=use_tools)

    entry = task_manager.create_chat_task(conv_id)
    # Lancer la génération sans attendre (fire & forget)
    entry.task = asyncio.create_task(_run_chat_generation(entry, conv_id, messages, config, use_tools, limits))
    return entry.task_id


async def _run_chat_generation(
    entry: ChatTaskEntry,
    conv_id: int,
    messages: list,
    config: dict,
    use_tools: bool,
    limits: AgentLimits,
):
    """
    Coroutine arrière-plan : génère la réponse (boucle agent avec outils), accumule tokens et événements
    dans le buffer de l'entrée et sauvegarde la réponse complète en BDD à la fin.
    S'exécute indépendamment de la connexion HTTP du client.
    """
    result = AgentResult()
    try:
        if not _orchestrator:
            entry.error = "Orchestrateur non initialisé"
            entry.status = "error"
            return

        await run_agent_turn(
            _orchestrator.ollama, _orchestrator._ollama_semaphore, messages, config,
            emit_text=lambda t: task_manager.append_chat_token(entry, t),
            emit_event=lambda e: task_manager.append_chat_token(entry, e),
            use_tools=use_tools, conversation_id=conv_id, limits=limits,
            lang=_chat_lang_from_config(), result=result,
        )
        _save_answer(conv_id, result)
        entry.status = "done"

    except asyncio.CancelledError:
        # Arrêt demandé : ce qui a été écrit, consulté ou proposé reste dans la conversation
        if result.text.strip() or result.tools:
            _save_answer(conv_id, result)
        entry.status = "done"

    except Exception as e:
        logger.error("ChatRouter", f"Erreur génération arrière-plan : {str(e)}")
        # Une action proposée doit rester décidable, même si la réponse a échoué ensuite
        if result.action_ids:
            _save_answer(conv_id, result)
        entry.error = str(e)
        entry.status = "error"


def _save_answer(conv_id: int, result: AgentResult):
    """Sauvegarde BDD (la trace des outils permet de rejouer leurs résultats aux tours suivants)."""
    metadata = {"tools": result.tools, "actions": result.action_ids} if result.tools else None
    with SessionLocal() as save_db:
        save_db.add(ChatMessage(conversation_id=conv_id, role="assistant", content=result.text,
                                metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None))
        save_db.commit()
    logger.info("ChatRouter", f"Réponse sauvegardée ({len(result.text)} car., {len(result.tools)} appel(s) d'outils)")


def _chat_lang_from_config() -> str:
    with SessionLocal() as s:
        return _chat_lang(s.query(GlobalConfig).first())


@router.post("/api/investigate")
async def investigate(data: dict, db: Session = Depends(get_db)):
    """Crée une conversation liée à une analyse et lance une investigation guidée (limites d'outils élargies)."""
    analysis = db.query(Analysis).filter(Analysis.id == data.get("analysis_id")).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="analysis_not_found")
    lang = _chat_lang(db.query(GlobalConfig).first())
    conv = ChatConversation(title=f"Investigation #{analysis.detection_id or analysis.id}", analysis_id=analysis.id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    prompt = INVESTIGATION_PROMPT.get(lang, INVESTIGATION_PROMPT["fr"]).format(analysis_id=analysis.id, rule_id=analysis.rule_id)
    db.add(ChatMessage(conversation_id=conv.id, role="user", content=prompt,
                       metadata_json=json.dumps({"kind": "investigation_plan"})))
    db.commit()
    # Assez de tours pour lire aussi les logs du conteneur ou le journal du service, avant le rapport
    task_id = await _start_generation(conv.id, db, AgentLimits(max_rounds=8, max_calls=14))
    return {"status": "ok", "id": conv.id, "task_id": task_id}


@router.get("/api/actions/{action_id}")
async def get_action(action_id: int, db: Session = Depends(get_db)):
    act = db.query(ChatAction).filter(ChatAction.id == action_id).first()
    if not act:
        raise HTTPException(status_code=404, detail="action_not_found")
    return action_to_dict(act, _chat_lang(db.query(GlobalConfig).first()))


@router.post("/api/actions/{action_id}")
async def decide_action(action_id: int, data: dict, db: Session = Depends(get_db)):
    """Confirmation explicite de l'utilisateur : 'apply' exécute l'action proposée, 'reject' la refuse."""
    act = db.query(ChatAction).filter(ChatAction.id == action_id).first()
    if not act:
        raise HTTPException(status_code=404, detail="action_not_found")
    decision = data.get("decision")
    if decision == "reject":
        if act.status == "pending":
            act.status = "rejected"
            act.decided_at = datetime.utcnow()
            db.commit()
        return action_to_dict(act, _chat_lang(db.query(GlobalConfig).first()))
    if decision != "apply":
        raise HTTPException(status_code=400, detail="invalid_decision")
    await apply_action(action_id)
    db.expire_all()
    act = db.query(ChatAction).filter(ChatAction.id == action_id).first()
    return action_to_dict(act, _chat_lang(db.query(GlobalConfig).first()))


@router.post("/api/stop/{task_id}")
async def stop_generation(task_id: str):
    """Arrête la génération : le modèle est libéré pour les autres services."""
    entry = task_manager.get_chat_task(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="task_not_found")
    if entry.task and not entry.task.done():
        entry.task.cancel()
        await asyncio.wait({entry.task}, timeout=5)
    return {"status": "ok"}


@router.get("/api/stream/{task_id}")
async def stream_chat(task_id: str, from_pos: int = 0):
    """
    Endpoint SSE reconnectable : diffuse les tokens d'une tâche chat.
    Le paramètre `from_pos` permet de reprendre là où le client s'est arrêté.
    La connexion reste ouverte jusqu'à ce que la génération soit terminée.
    Les éléments non textuels du buffer (appels d'outils, actions proposées) sont envoyés comme `event`.
    """
    entry = task_manager.get_chat_task(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="task_not_found")

    async def generator():
        pos = from_pos
        idle = 0.0
        while True:
            buf = entry.token_buffer
            if pos < len(buf):
                idle = 0.0
                # Envoyer les tokens en attente (rattrapage ou nouveaux)
                for token in buf[pos:]:
                    pos += 1
                    if isinstance(token, dict):
                        yield f"data: {json.dumps({'event': token, 'pos': pos})}\n\n"
                    else:
                        yield f"data: {json.dumps({'text': token, 'pos': pos})}\n\n"
            elif entry.status == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            elif entry.status == "error":
                yield f"data: {json.dumps({'error': entry.error})}\n\n"
                break
            else:
                # Attendre de nouveaux tokens (polling léger)
                if await task_manager.wait_for_token(entry, timeout=2.0):
                    idle = 0.0
                else:
                    idle += 2.0
                    # Commentaire SSE pendant les longues attentes (modèle occupé par une analyse,
                    # chargement…) : sinon un proxy ou le navigateur ferme la connexion inactive
                    if idle >= 14.0:
                        idle = 0.0
                        yield ": keepalive\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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

    db.query(ChatAction).filter(ChatAction.conversation_id == conv_id).delete()
    db.delete(conv)
    db.commit()
    return {"status": "ok"}


@router.post("/api/regenerate/{conv_id}")
async def regenerate_last(conv_id: int, db: Session = Depends(get_db)):
    """
    Supprime le dernier message assistant de la conversation et relance la génération,
    sans ajouter de nouveau message utilisateur.
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

    last_user = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .first()
    )
    if not last_user:
        raise HTTPException(status_code=404, detail="Aucun message utilisateur trouvé")

    return {"status": "ok", "task_id": await _start_generation(conv_id, db)}


@router.post("/api/auto-title/{conv_id}")
async def auto_title_conversation(conv_id: int, db: Session = Depends(get_db)):
    """
    Génère automatiquement un titre court pour la conversation
    à partir du premier échange (1er message user + 1ère réponse assistant).
    Si la conversation est liée à une analyse, préfixe avec [#detection_id].
    """
    conv = db.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(4)
        .all()
    )

    user_msg = next((m for m in messages if m.role == "user"), None)
    ai_msg = next((m for m in messages if m.role == "assistant"), None)

    if not user_msg or not ai_msg:
        return {"title": conv.title}

    # Construire le mini-prompt de titrage
    excerpt_user = user_msg.content[:300]
    excerpt_ai = ai_msg.content[:300]
    title_prompt = (
        "Tu dois générer un titre ultra-court (4 à 6 mots maximum) pour cette conversation de diagnostic système.\n"
        "Écris-le dans la langue de la question.\n"
        "Réponds UNIQUEMENT avec le titre, sans ponctuation finale, sans guillemets, sans explication.\n\n"
        f"Question : {excerpt_user}\n"
        f"Réponse : {excerpt_ai}\n\n"
        "Titre :"
    )

    cfg = db.query(GlobalConfig).first()
    ollama_url   = (cfg.ollama_url   or "http://ollama:11434") if cfg else "http://ollama:11434"
    ollama_model = (cfg.ollama_model or "gemma4:e4b")         if cfg else "gemma4:e4b"

    generated_title = ""
    try:
        if _orchestrator:
            async with _orchestrator._ollama_semaphore:
                generated_title = await _orchestrator.ollama.analyze_async(
                    prompt=title_prompt,
                    url=ollama_url,
                    model=ollama_model,
                    # Même contexte que le reste : un autre recharge le modèle, ici puis à la question suivante (environ 57 s)
                    options=llm_options(config_to_dict(cfg), temperature=0.3),
                    think=False
                )
    except Exception as e:
        logger.error("ChatRouter", f"Auto-title error: {e}")
        return {"title": conv.title}

    # Nettoyer le titre généré
    generated_title = generated_title.strip().strip('"\'\'"').rstrip('.!?')
    # Garder seulement la première ligne
    generated_title = generated_title.split('\n')[0].strip()
    if not generated_title:
        return {"title": conv.title}

    # Préfixer avec l'ID de détection si disponible
    if conv.analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first()
        if analysis and analysis.detection_id:
            generated_title = f"[#{analysis.detection_id}] {generated_title}"

    # Sauvegarder en BDD
    conv.title = generated_title
    db.commit()

    return {"title": generated_title}


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
