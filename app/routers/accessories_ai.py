"""POST /ai/recommend-accessories, POST /ai/overlay-accessory — multi-agent workflow + Gemini render."""

import base64

import json

import logging

import os

import re

from typing import Any, Optional



import google.generativeai as genai

import httpx

from fastapi import APIRouter, Header, HTTPException

from pydantic import BaseModel

from supabase import Client, create_client



import asyncio

from app.services.accessory_overlay_handler import execute_overlay

OVERLAY_TOTAL_TIMEOUT_S = 180



logger = logging.getLogger(__name__)



genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

ai_router = APIRouter(prefix="/ai", tags=["ai"])



RENDER_MODEL = "gemini-2.0-flash-exp"





def _anon() -> Client:

    return create_client(

        os.environ["SUPABASE_URL"],

        os.environ.get("SUPABASE_ANON_KEY") or os.environ["SUPABASE_SERVICE_KEY"],

    )





def _service() -> Client:

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])





def _img_part(url: str) -> dict:

    if url.startswith("data:"):

        header, b64data = url.split(",", 1)

        mime = "image/jpeg"

        if ":" in header:

            mime = header.split(":")[1].split(";")[0] or mime

        return {"inline_data": {"mime_type": mime, "data": b64data}}

    data = httpx.get(url, timeout=20, follow_redirects=True).content

    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode()}}





class RecommendReq(BaseModel):

    dress_image_url: str





@ai_router.post("/recommend-accessories")

async def recommend_accessories(body: RecommendReq):

    sb = _anon()

    model = genai.GenerativeModel("gemini-1.5-flash")

    img_part = _img_part(body.dress_image_url)

    prompt = """Analyze this dress image and respond ONLY in JSON (no markdown):

{"dress_color":"<color>","fabric_type":"<type>","dress_style":"<casual|formal|bridal|traditional>",

"embroidery_level":"<none|low|medium|high>","suggested_accessory_keywords":["kw1","kw2","kw3"]}"""

    resp = model.generate_content([prompt, img_part])

    raw = re.sub(r"```json|```", "", resp.text.strip()).strip()

    try:

        analysis = json.loads(raw)

    except json.JSONDecodeError:

        analysis = {"suggested_accessory_keywords": ["lace", "beads"]}



    style = analysis.get("dress_style", "casual").lower()

    cats = {

        "bridal": ["Lace", "Beads", "Patches", "Sequins"],

        "formal": ["Patches", "Sequins", "Tassels", "Lace"],

        "traditional": ["Lace", "Patches", "Buttons", "Tassels"],

    }.get(style, ["Buttons", "Beads", "Sequins", "Lace"])



    ids: list[str] = []

    for cat in cats[:3]:

        r = (

            sb.table("accessories")

            .select("id")

            .eq("is_active", True)

            .ilike("category", cat)

            .limit(4)

            .execute()

        )

        for row in r.data or []:

            if row["id"] not in ids:

                ids.append(row["id"])

    for kw in analysis.get("suggested_accessory_keywords", [])[:3]:

        r = (

            sb.table("accessories")

            .select("id")

            .eq("is_active", True)

            .ilike("title", f"%{kw}%")

            .limit(3)

            .execute()

        )

        for row in r.data or []:

            if row["id"] not in ids:

                ids.append(row["id"])



    return {"analysis": analysis, "recommended_ids": ids[:12]}





def _normalize_placement_mode(

    placement_mode: str,

    placement_method: str | None,

    has_region: bool,

    has_reference: bool,

) -> str:

    method = (placement_method or placement_mode or "auto").strip().lower()

    aliases = {

        "region": "roi",

        "roi": "roi",

        "reference": "reference_image",

        "reference_image": "reference_image",

        "auto": "auto",

    }

    resolved = aliases.get(method, "auto")

    if resolved == "reference_image" and has_reference:

        return "reference_image"

    if resolved == "roi" and has_region:

        return "roi"

    if resolved in ("reference_image", "roi") and not (has_reference or has_region):

        return "auto"

    return resolved





class OverlayReq(BaseModel):

    dress_image_url: str

    accessory_id: str

    placement_mode: str = "auto"

    placement_method: Optional[str] = None

    region_of_interest: Optional[dict] = None

    mask_data: Optional[str] = None

    reference_image_url: Optional[str] = None

    written_prompt_for_reference: Optional[str] = None

    clarification_prompt: Optional[str] = None

    placement_prompt_text: Optional[str] = None

    written_prompt_for_accessory: Optional[str] = None

    user_return_prompt_text: Optional[str] = None

    selected_variant: Optional[str] = None

    skip_variant_check: bool = False

    workflow_state: Optional[dict[str, Any]] = None
    use_multi_agent: bool = False


def _user_prompt(body: OverlayReq) -> str:
    parts = [
        body.user_return_prompt_text,
        body.placement_prompt_text,
        body.written_prompt_for_accessory,
    ]
    return " ".join(p.strip() for p in parts if p and p.strip())


def _optional_user_id(authorization: str | None = Header(None)) -> str | None:

    if not authorization or not authorization.lower().startswith("bearer "):

        return None

    try:

        from app.deps import get_settings

        import jwt



        token = authorization.split(" ", 1)[1].strip()

        settings = get_settings()

        if settings.jwt_secret:

            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

        else:

            payload = jwt.decode(token, options={"verify_signature": False})

        uid = payload.get("sub") or payload.get("user_id") or payload.get("id")

        return str(uid) if uid else None

    except Exception:

        return None





@ai_router.post("/overlay-accessory")

@ai_router.post("/generate-accessory-overlay")

async def overlay_accessory(

    body: OverlayReq,

    authorization: str | None = Header(None),

):

    sb = _service()

    acc = sb.table("accessories").select("*").eq("id", body.accessory_id).single().execute()

    if not acc.data:

        raise HTTPException(404, "Accessory not found")

    accessory = acc.data

    placement_method = _normalize_placement_mode(
        body.placement_mode,
        body.placement_method,
        bool(body.region_of_interest),
        bool(body.reference_image_url),
    )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(execute_overlay, body=body, accessory=accessory),
            timeout=OVERLAY_TOTAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            504,
            f"Overlay timed out after {OVERLAY_TOTAL_TIMEOUT_S}s. Try a smaller dress image or retry.",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    if result.get("needs_variant_selection"):
        return result

    user_id = _optional_user_id(authorization)
    agent_summary = result.get("agent_summary") or {}

    if user_id and result.get("imageDataUrl"):
        try:
            from app.services.user_history_store import insert_user_history

            insert_user_history(
                user_id=user_id,
                activity_type="accessory_overlay",
                input_details={
                    "accessory_id": body.accessory_id,
                    "accessory_title": accessory.get("title"),
                    "placement_mode": body.placement_mode,
                    "placement_method": placement_method,
                    "selected_variant": agent_summary.get("variant"),
                    "fast_mode": agent_summary.get("fast_mode", True),
                },
                output_details={
                    "summary": (
                        f"Accessory overlay — {accessory.get('title', 'accessory')}"
                        + (
                            f" ({agent_summary['variant']})"
                            if agent_summary.get("variant")
                            else ""
                        )
                    ),
                    "image_generated": True,
                    "agent_summary": agent_summary,
                },
                related_resource_id=None,
            )
        except Exception as exc:
            logger.warning("user_history log failed: %s", exc)

    return result


