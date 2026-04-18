#!/usr/bin/env python3
"""
FastAPI triage server for Gemma-4-26B.
Lightweight HTTP client -- sends requests to vLLM's OpenAI-compatible API.
"""

import base64
import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from typing import Optional

import anyio
import httpx
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel

logger = logging.getLogger("api_server")

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://gemma4:8000")
MODEL_NAME = os.environ.get("MODEL_NAME", "gemma-4-26b-a4b-it")

MAX_IMAGE_DIM = 1920
MAX_IMAGES_PER_REQUEST = 8

# ---------- System prompts ----------

PROMPT_SINGLE = """You are a visual weapons detection system.

You are given one image from a security camera detection event.

Your task is to determine if a weapon is visibly present.

Rules:
- A weapon must be reported if it is clearly visible
- Partial visibility is acceptable if the object matches a weapon profile
- Do NOT infer weapons from clothing, role, or environment
- A holstered weapon is NOT a visible weapon unless the weapon itself is clearly visible outside the holster
- lawEnforcementIndicators is advisory only and must not influence visibleWeapon

Always complete all fields, even if no weapon is detected. The confidence field represents your confidence in your conclusion, whether that conclusion is weapon or clean.

You MUST return a JSON object with exactly these fields:
{"visibleWeapon": bool, "weaponTypes": [strings], "confidence": float, "lawEnforcementIndicators": bool, "environmentType": string, "personDescription": string}"""

PROMPT_MULTI = """You are a visual weapons detection system.

You are given multiple images from the same event.
Image 1 is the full scene at the time of detection.
All other images are cropped views of the SAME person across nearby frames.

Your task is to determine if a weapon is visibly present.

Rules:
- A weapon must be reported if it is clearly visible in ANY cropped image
- A single clear frame is sufficient
- Partial visibility is acceptable if the object matches a weapon profile
- Evaluate each cropped image independently and focus on the clearest evidence
- Ignore blurry or unclear frames
- Do NOT infer weapons from clothing, role, or environment
- Do NOT suppress a detection because other frames do not show a weapon
- A holstered weapon is NOT a visible weapon unless the weapon itself is clearly visible outside the holster
- lawEnforcementIndicators is advisory only and must not influence visibleWeapon

Always complete all fields, even if no weapon is detected. The confidence field represents your confidence in your conclusion, whether that conclusion is weapon or clean.

You MUST return a JSON object with exactly these fields:
{"visibleWeapon": bool, "weaponTypes": [strings], "confidence": float, "lawEnforcementIndicators": bool, "environmentType": string, "personDescription": string}"""

USER_PROMPT_SINGLE = "Analyze this security camera image for potential weapons."
USER_PROMPT_MULTI = (
    "Analyze these security camera images for potential weapons. "
    "Image 1 is the full scene; remaining images are cropped views of "
    "the detected person across nearby frames."
)

# ---------- Response models ----------

class TriageResponse(BaseModel):
    event_id: str
    visible_weapon: bool = False
    weapon_types: list[str] = []
    confidence: float = 0.0
    law_enforcement: bool = False
    environment_type: str = "unknown"
    person_description: str = ""
    should_alert: bool = False
    requires_review: bool = False
    error: Optional[str] = None
    inference_time_ms: float = 0.0
    images_used: int = 0

# ---------- Image helpers ----------

def image_to_data_uri(image_bytes: bytes) -> str:
    """Resize if needed, convert to RGB, and return a base64 data URI."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB",):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIM:
        if w > h:
            new_w, new_h = MAX_IMAGE_DIM, int(h * MAX_IMAGE_DIM / w)
        else:
            new_h, new_w = MAX_IMAGE_DIM, int(w * MAX_IMAGE_DIM / h)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"

# ---------- Message construction ----------

def build_messages(anchor_uri: str, crop_uris: list[str]) -> list[dict]:
    if crop_uris:
        content = [{"type": "image_url", "image_url": {"url": anchor_uri}}]
        for uri in crop_uris:
            content.append({"type": "image_url", "image_url": {"url": uri}})
        content.append({"type": "text", "text": USER_PROMPT_MULTI})
        system_prompt = PROMPT_MULTI
    else:
        content = [
            {"type": "image_url", "image_url": {"url": anchor_uri}},
            {"type": "text", "text": USER_PROMPT_SINGLE},
        ]
        system_prompt = PROMPT_SINGLE

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

# ---------- Response parsing ----------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB per request
MAX_ZIP_BYTES = 50 * 1024 * 1024     # 50MB per zip

def parse_bool(value) -> bool:
    """Safely parse a boolean from JSON that might return a string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)

def clamp_confidence(value) -> float:
    """Parse confidence and clamp to [0.0, 1.0]."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not (0.0 <= f <= 1.0):
        return max(0.0, min(1.0, f))
    return f

def parse_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = [l for l in cleaned.split("\n") if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    parsed = json.loads(cleaned)

    return {
        "visibleWeapon": parse_bool(parsed.get("visibleWeapon", parsed.get("hasWeapon", False))),
        "weaponTypes": parsed.get("weaponTypes", []),
        "confidence": clamp_confidence(parsed.get("confidence", 0.0)),
        "lawEnforcementIndicators": parse_bool(parsed.get("lawEnforcementIndicators", False)),
        "environmentType": parsed.get("environmentType", "unknown"),
        "personDescription": parsed.get("personDescription", ""),
    }

# ---------- App ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        base_url=VLLM_BASE_URL,
        timeout=60.0,
    )
    logger.info(f"Triage server ready, vLLM backend: {VLLM_BASE_URL}")
    yield
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "backend": VLLM_BASE_URL}

@app.get("/status")
async def status():
    try:
        resp = await app.state.http_client.get("/metrics")
        resp.raise_for_status()
        metrics_text = resp.text

        def parse_metric(name):
            for line in metrics_text.split("\n"):
                if line.startswith(name + "{") or line.startswith(name + " "):
                    return float(line.split()[-1])
            return None

        running = parse_metric("vllm:num_requests_running")
        waiting = parse_metric("vllm:num_requests_waiting")
        kv_cache = parse_metric("vllm:kv_cache_usage_perc")

        healthy = running is not None  # metrics parsed successfully

        return {
            "healthy": healthy,
            "requests_running": int(running) if running is not None else None,
            "requests_waiting": int(waiting) if waiting is not None else None,
            "kv_cache_usage": round(kv_cache, 3) if kv_cache is not None else None,
            "model": MODEL_NAME,
        }
    except Exception as e:
        return {
            "healthy": False,
            "requests_running": None,
            "requests_waiting": None,
            "kv_cache_usage": None,
            "model": MODEL_NAME,
            "error": str(e),
        }

async def run_triage(anchor_bytes: bytes, crop_bytes_list: list[bytes], event_id: str) -> TriageResponse:
    """Shared inference logic for both form-data and zip inputs."""
    if not event_id:
        event_id = str(uuid.uuid4())[:8]

    start = time.time()
    n_images = 1 + len(crop_bytes_list)

    try:
        anchor_uri = await anyio.to_thread.run_sync(image_to_data_uri, anchor_bytes)
        crop_uris = [await anyio.to_thread.run_sync(image_to_data_uri, b) for b in crop_bytes_list]

        messages = build_messages(anchor_uri, crop_uris)

        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 512,
            "response_format": {
                "type": "json_object",
            },
        }

        last_error = None

        for attempt in range(2):
            resp = await app.state.http_client.post(
                "/v1/chat/completions",
                json=payload,
            )

            elapsed_ms = (time.time() - start) * 1000

            if resp.status_code != 200:
                last_error = f"vLLM returned {resp.status_code}: {resp.text}"
                continue

            raw_text = resp.json()["choices"][0]["message"]["content"]

            try:
                parsed = parse_response(raw_text)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = f"Parse failed (attempt {attempt + 1}): {e} | raw: {raw_text}"
                logger.warning(last_error)
                continue

            return TriageResponse(
                event_id=event_id,
                visible_weapon=parsed["visibleWeapon"],
                weapon_types=parsed["weaponTypes"],
                confidence=parsed["confidence"],
                law_enforcement=parsed["lawEnforcementIndicators"],
                environment_type=parsed["environmentType"],
                person_description=parsed["personDescription"],
                should_alert=parsed["visibleWeapon"],
                inference_time_ms=elapsed_ms,
                images_used=n_images,
            )

        # Both attempts failed
        elapsed_ms = (time.time() - start) * 1000
        return TriageResponse(
            event_id=event_id,
            should_alert=True,
            requires_review=True,
            error=last_error,
            inference_time_ms=elapsed_ms,
            images_used=n_images,
        )

    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        logger.error(f"Triage error: {e}")
        return TriageResponse(
            event_id=event_id,
            should_alert=True,
            requires_review=True,
            error=str(e),
            inference_time_ms=elapsed_ms,
            images_used=n_images,
        )


def extract_images_from_zip(zip_bytes: bytes) -> tuple[bytes, list[bytes]]:
    """Extract anchor and crops from a zip file.
    Anchor: filename ending in _O.jpg or O.jpg
    Crops: filenames ending in _C1.jpg, _C2.jpg, etc. or C1.jpg, C2.jpg, etc.
    Skips: _A.jpg (annotations) and anything else
    """
    anchor = None
    crops = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist())
        for name in names:
            basename = os.path.basename(name).lower()
            if not basename.endswith(".jpg") and not basename.endswith(".jpeg") and not basename.endswith(".png"):
                continue
            if basename == "o.jpg" or basename.endswith("_o.jpg"):
                anchor = zf.read(name)
            elif re.match(r"(.*_)?c\d+\.jpe?g$", basename):
                crops.append((basename, zf.read(name)))

    if anchor is None:
        raise HTTPException(status_code=400, detail="Zip file must contain an anchor image (O.jpg or *_O.jpg)")

    # Sort crops by name to maintain order (C1, C2, C3...)
    crops.sort(key=lambda x: x[0])
    return anchor, [data for _, data in crops]


@app.post("/v1/triage", response_model=TriageResponse)
async def triage(
    anchor: Optional[UploadFile] = File(default=None),
    crops: list[UploadFile] = File(default=[]),
    package: Optional[UploadFile] = File(default=None),
    event_id: str = Form(default=""),
):
    if package is not None and anchor is not None:
        raise HTTPException(status_code=400, detail="Provide 'anchor' or 'package', not both")

    if package is not None:
        zip_bytes = await package.read()
        if len(zip_bytes) > MAX_ZIP_BYTES:
            raise HTTPException(status_code=413, detail=f"Zip file exceeds {MAX_ZIP_BYTES // (1024*1024)}MB limit")
        anchor_bytes, crop_bytes_list = extract_images_from_zip(zip_bytes)
        if len(crop_bytes_list) + 1 > MAX_IMAGES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"Too many images: {len(crop_bytes_list) + 1} exceeds limit of {MAX_IMAGES_PER_REQUEST}")
        return await run_triage(anchor_bytes, crop_bytes_list, event_id)

    if anchor is not None:
        anchor_bytes = await anchor.read()
        if len(anchor_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Anchor image exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")
        if 1 + len(crops) > MAX_IMAGES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"Too many images: {1 + len(crops)} exceeds limit of {MAX_IMAGES_PER_REQUEST}")
        crop_bytes_list = []
        for c in crops:
            cb = await c.read()
            if len(cb) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"Crop image exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")
            crop_bytes_list.append(cb)
        return await run_triage(anchor_bytes, crop_bytes_list, event_id)

    raise HTTPException(status_code=400, detail="Provide either 'anchor' (with optional 'crops') or 'package' (zip file)")


@app.post("/v1/triage/image", response_model=TriageResponse)
async def triage_image(request: Request, event_id: Optional[str] = None):
    """Accept a single raw image (POST body is the image bytes)."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Image exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")
    return await run_triage(body, [], event_id or "")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8001)
