import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings


async def save_upload(user_id: str, subfolder: str, file: UploadFile, ext: str | None = None) -> str:
    settings = get_settings()
    base = Path(settings.upload_dir) / user_id / subfolder
    base.mkdir(parents=True, exist_ok=True)
    suffix = ext or Path(file.filename or "img").suffix or ".jpg"
    if not suffix.startswith("."):
        suffix = "." + suffix
    name = f"{uuid.uuid4().hex}{suffix}"
    dest = base / name
    content = await file.read()
    dest.write_bytes(content)
    rel = f"{user_id}/{subfolder}/{name}".replace("\\", "/")
    return rel


def public_url(relative_path: str) -> str:
    settings = get_settings()
    return f"{settings.public_base_url}/static/uploads/{relative_path}"
