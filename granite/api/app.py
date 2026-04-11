"""Granite CRM API — минимальный FastAPI для аутрича.

Запуск: python cli.py api
   или: uvicorn granite.api.app:app --reload
"""
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте, очистка при остановке."""
    import os
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    config_path = os.environ.get("GRANITE_CONFIG", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = config.get("database", {}).get("path", "data/granite.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Session = sessionmaker(bind=engine)

    app.state.engine = engine
    app.state.Session = Session
    app.state.config = config

    logger.info(f"CRM API started. DB: {db_path}")

    # Файловый лог с ротацией
    logger.add(
        "data/crm.log",
        rotation="10 MB",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
    )

    yield

    engine.dispose()
    logger.info("CRM API stopped.")


app = FastAPI(title="Granite CRM API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from granite.api import companies, touches, tasks, tracking, campaigns, followup, funnel
app.include_router(companies.router, prefix="/api/v1", tags=["companies"])
app.include_router(touches.router, prefix="/api/v1", tags=["touches"])
app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(tracking.router, prefix="/api/v1", tags=["tracking"])
app.include_router(campaigns.router, prefix="/api/v1", tags=["campaigns"])
app.include_router(followup.router, prefix="/api/v1", tags=["followup"])
app.include_router(funnel.router, prefix="/api/v1", tags=["funnel"])


@app.get("/health")
def health():
    return {"status": "ok"}
