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
from app.services.log_watcher import LogWatcher
from app.services.orchestrator import Orchestrator

# ── Instances globales ──
orchestrator = Orchestrator()
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

    # Démarre le watcher en background
    watcher_task = asyncio.create_task(log_watcher.start())
    print("[Main] LogWatcher démarré en background")

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

# ── Routers ──
app.include_router(rules.router)
app.include_router(config.router)
app.include_router(dashboard.router)
app.include_router(files_router.router)
app.include_router(monitor_router.router)
app.include_router(chat_router.router)


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
