"""
StitchMate unified backend API (port 8000).

Includes: auth, tailor onboarding, accessories marketplace, fabric/workflow (SQLAlchemy).
Run: uvicorn main:app --reload --port 8000
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir / ".env")
# Fill empty backend keys from bolt-stichmate/.env (e.g. APIFY_API_TOKEN)
_bolt_env = _backend_dir.parent / "bolt-stichmate" / ".env"
if _bolt_env.exists():
    from dotenv import dotenv_values

    for key, value in dotenv_values(_bolt_env).items():
        if value and not (os.getenv(key) or "").strip():
            os.environ[key] = value

from auth import router as auth_router  # noqa: E402
from tailor import router as tailor_router  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.routers import fabric, workflow, user_history  # noqa: E402
from app.routers.accessories import acc_router  # noqa: E402
from app.routers.accessories_sync import sync_router  # noqa: E402
from app.routers.accessories_ai import ai_router  # noqa: E402
from app.routers.design_studio_ai import studio_ai_router  # noqa: E402
from app.routers.payments import router as payments_router  # noqa: E402
from app.routers.tailor_voice import router as tailor_voice_router  # noqa: E402
from app.routers.riders import router as riders_router  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="StitchMate Backend API",
    description="Auth, tailor, accessories, fabric upload & design workflow",
    version="1.1.0",
)

_DEFAULT_CORS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:8080,http://127.0.0.1:8080,"
    "https://stitch-mate-ten.vercel.app"
)
_cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", _DEFAULT_CORS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Platform (Supabase JWT)
app.include_router(auth_router)
app.include_router(tailor_router)

# Accessories marketplace (Supabase catalog + Apify sync + Gemini overlay)
app.include_router(acc_router)
app.include_router(sync_router)
app.include_router(ai_router)
app.include_router(studio_ai_router)

# Fabric & design workflow (local SQLite + uploads)
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
app.include_router(fabric.router)
app.include_router(workflow.router)
app.include_router(user_history.router)
app.include_router(payments_router)
app.include_router(tailor_voice_router)
app.include_router(riders_router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    logger.info("StitchMate API ready; fabric uploads=%s", settings.upload_dir)


@app.get("/")
def root():
    return {
        "message": "StitchMate Backend API",
        "version": "1.1.0",
        "services": {
            "auth": "/api/auth",
            "tailor": "/api/tailor",
            "accessories": "/accessories",
            "admin_sync": "/admin/sync-accessories",
            "ai": "/ai",
            "fabric": "/api/fabric",
            "workflow": "/api",
            "user_history": "/api/user-history",
            "payments": "/api/payments",
            "riders": "/api/riders",
        },
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "stitchmate-api"}
