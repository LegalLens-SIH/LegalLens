"""
LegalLense backend - minimal FastAPI app.

Scope: OCR and deterministic Legal Metrology compliance evaluation.
    LegalLense Upload UI -> POST /api/scans -> OCR JSON -> /api/compliance/evaluate

Run from the `stitch_legallense_compliance_portal` directory:
    backend\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8000

Serve `frontend` as the static web root for the browser UI.

The ruleset is loaded from backend/rules and compliance results include an
auditable ruleset and engine version.
"""

from __future__ import annotations

import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env BEFORE any backend.* import below. Several modules (e.g.
# backend/ocr/yolo_service.py's YOLO_MODEL_PATH/YOLO_ENABLED/
# YOLO_CONFIDENCE_THRESHOLD/YOLO_DEVICE/YOLO_DETECTION_MODE) read
# os.getenv(...) at MODULE level - once, at import time - not lazily inside
# a function. If .env is loaded after those imports run, those constants
# permanently bake in whatever os.environ looked like pre-.env (their
# hardcoded Python defaults), silently ignoring .env for the lifetime of the
# process. Confirmed as a real, previously-silent bug: YOLO_DETECTION_MODE
# in .env had no effect until this load_dotenv() call was moved here.
# (backend/api/auth.py and backend/database.py's os.getenv calls are inside
# function bodies called at request/startup time, not module level, so they
# were never affected - this fix is specifically for yolo_service.py's
# module-level reads, but is placed first for any future module that adds
# one.)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.ocr import router as ocr_router
from backend.api.auth import router as auth_router
from backend.api.manufacturer import router as manufacturer_router
from backend.api.compliance import router as compliance_router
from backend.api.official import router as official_router
from backend.database import close_mongodb, connect_to_mongodb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    connect_to_mongodb()
    try:
        yield
    finally:
        close_mongodb()


app = FastAPI(
    title="LegalLense Backend",
    description="OCR integration layer for the LegalLense compliance portal.",
    version="0.1.0",
    lifespan=lifespan,
)

# The frontend is plain static HTML with no fixed dev-server port (it can be
# opened via VS Code Live Server, `python -m http.server`, `npx serve`,
# etc.), so any localhost/127.0.0.1 origin is allowed for local development.
# This is intentionally permissive for local dev only - it must be locked
# down to a specific origin before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"(null|http://(localhost|127\.0\.0\.1)(:\d+)?)",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(ocr_router)
app.include_router(auth_router)
app.include_router(manufacturer_router)
app.include_router(compliance_router)
app.include_router(official_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "legallense-backend"}
