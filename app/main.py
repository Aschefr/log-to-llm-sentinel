import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

from app.database import init_db
from app.routers import rules, config, dashboard
from app.routers import files as files_router
from app.routers import monitor as monitor_router
from app.routers import chat as chat_router
from app.routers import i18n as i18n_router
from app.services.log_watcher import LogWatcher
from app.services.orchestrator import Orchestrator
from app.services.task_manager import task_manager
from app.services.meta_service import MetaAnalysisService

# ── Instances globales ──
orchestrator = Orchestrator()
meta_service = MetaAnalysisService(orchestrator=orchestrator)

log_watcher = LogWatcher(on_new_lines=orchestrator.handle_new_lines)
watcher_task = None


# ── Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gère le démarrage et l'arrêt propre du watcher."""
    global watcher_task

    # Startup
    init_db()
    os.makedirs("/logs", exist_ok=True)
    os.makedirs("./data", exist_ok=True)

    # Partager l'orchestrateur avec les routers (pour accès aux buffers et Ollama)
    monitor_router.set_orchestrator(orchestrator)
    chat_router.set_orchestrator(orchestrator)
    config.set_orchestrator(orchestrator)
    rules.set_orchestrator(orchestrator)
    webhook_router.set_orchestrator(orchestrator)

    # Démarre le watcher en background
    watcher_task = asyncio.create_task(log_watcher.start())
    print("[Main] LogWatcher démarré en background")

    # Reprendre les sessions d'auto-apprentissage interrompues par un redémarrage
    from app.services.keyword_learning_service import resume_stuck_sessions
    asyncio.create_task(resume_stuck_sessions())

    # Nettoyage périodique des tâches d'arrière-plan (toutes les 30 min)
    async def _cleanup_tasks():
        while True:
            await asyncio.sleep(1800)
            removed = task_manager.cleanup_old_tasks(max_age_hours=2)
            if removed:
                print(f"[Main] Nettoyage tâches : {removed} entrée(s) supprimée(s)")

    async def _run_meta_analyses_loop():
        while True:
            await asyncio.sleep(600)  # Vérifie toutes les 10 minutes
            await meta_service.run_scheduled_analyses()

    asyncio.create_task(_cleanup_tasks())
    asyncio.create_task(_run_meta_analyses_loop())

    yield

    # Shutdown
    log_watcher.stop()
    if watcher_task:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
    print("[Main] Arrêt propre terminé")


app = FastAPI(title="Log-to-LLM-Sentinel", lifespan=lifespan)

# ── Templates & Static ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ── Global Context (Version) ──
from app.version import get_app_version
@app.middleware("http")
async def add_version_to_context(request: Request, call_next):
    # This isn't the cleanest way for Jinja2 context but it works for global injection
    response = await call_next(request)
    return response

@app.get("/api/version")
def get_api_version():
    return {"version": get_app_version()}

# Pass version to all templates automatically
from app.version import get_app_version
templates.env.globals.update(app_version=get_app_version())

# ── Routers ──
from app.routers import chat as chat_router
from app.routers import i18n as i18n_router
from app.routers import meta_analysis as meta_router
from app.routers import keyword_learning as kw_learning_router
from app.routers import webhook as webhook_router

app.include_router(rules.router)
app.include_router(config.router)
app.include_router(dashboard.router)
app.include_router(files_router.router)
app.include_router(monitor_router.router)
app.include_router(chat_router.router)
app.include_router(i18n_router.router)
app.include_router(meta_router.router)
app.include_router(kw_learning_router.router)
app.include_router(webhook_router.router)


# ── Pages ──
@app.get("/")
def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/rules")
def rules_page(request: Request):
    return templates.TemplateResponse("rules.html", {"request": request})


@app.get("/config")
def config_page(request: Request):
    return templates.TemplateResponse("config.html", {"request": request})


@app.get("/monitor")
def monitor_page(request: Request):
    return templates.TemplateResponse("monitor.html", {"request": request})

@app.get("/meta-analysis")
def meta_analysis_page(request: Request):
    return templates.TemplateResponse("meta.html", {"request": request})
