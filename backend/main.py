"""FinAgent FastAPI Backend."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    from backend.db.models import init_db
    init_db()
    logger.info("FinAgent API started")

    # Auto-start scheduler if configured
    if os.getenv("SCHEDULER_ENABLED", "").lower() in ("1", "true", "yes"):
        from backend.api.scheduler_api import start_scheduler
        start_scheduler()
        logger.info("Scheduler auto-started via SCHEDULER_ENABLED")

    yield

    from backend.api.scheduler_api import stop_scheduler
    stop_scheduler()
    logger.info("FinAgent API shutting down")


app = FastAPI(title="FinAgent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Core routes
from backend.api.signals import router as signals_router
from backend.api.trades import router as trades_router
from backend.api.portfolio import router as portfolio_router
from backend.api.risk import router as risk_router

# Phase 3 routes
from backend.api.actions import router as actions_router
from backend.api.settings_api import router as settings_router
from backend.api.logs_api import router as logs_router

# Phase 4 routes
from backend.api.accounts_api import router as accounts_router

# Phase 5 routes
from backend.api.performance import router as performance_router
from backend.api.replay_api import router as replay_router

# Positions
from backend.api.positions_api import router as positions_router

# Scheduler
from backend.api.scheduler_api import router as scheduler_router

# Cache management
from backend.api.cache_api import router as cache_router

app.include_router(signals_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(accounts_router, prefix="/api")
app.include_router(performance_router, prefix="/api")
app.include_router(replay_router, prefix="/api")
app.include_router(positions_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(cache_router, prefix="/api")


@app.get("/health")
def health():
    try:
        from agents.llm import is_available, MODEL
        llm_info = {"model": MODEL, "available": is_available()}
    except Exception:
        llm_info = {"model": "qwen3:32b", "available": False}
    try:
        from backend.api.scheduler_api import _is_running
        scheduler_running = _is_running()
    except Exception:
        scheduler_running = False
    return {"status": "ok", "service": "finagent", "llm": llm_info, "scheduler": scheduler_running}
