import os

import jwt
from jwt import PyJWTError
from fastapi import Header, HTTPException

from app.config import get_settings


def _user_id_from_token(token: str) -> str:
    """Resolve user id from Supabase access token (preferred) or JWT payload."""
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from supabase_client import supabase

        user_resp = supabase.auth.get_user(token)
        if user_resp and user_resp.user and user_resp.user.id:
            return str(user_resp.user.id)
    except Exception:
        pass

    settings = get_settings()
    try:
        secret = (
            os.environ.get("SUPABASE_JWT_SECRET")
            or settings.jwt_secret
            or None
        )
        if secret and secret not in ("", "your_secretmkdir uploads"):
            payload = jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
        else:
            payload = jwt.decode(token, options={"verify_signature": False})
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    uid = payload.get("sub") or payload.get("user_id") or payload.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Token has no user id")
    return str(uid)


def get_current_user_id(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")
    return _user_id_from_token(token)
