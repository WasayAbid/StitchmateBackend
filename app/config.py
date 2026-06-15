import os
from functools import lru_cache


@lru_cache
def get_settings():
    class _Settings:
        gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        jwt_secret: str = os.getenv("JWT_SECRET", "")
        public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")
        database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/fabric.db")
        upload_dir: str = os.getenv("UPLOAD_DIR", "uploads")
        gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        gemini_max_retries: int = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
        gemini_timeout_seconds: int = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "90"))
        groq_api_key: str = os.getenv("GROQ_API_KEY", "") or os.getenv("VITE_GROQ_API_KEY", "")

    return _Settings()
