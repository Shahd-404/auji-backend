# ==============================================================
# 📁 app/main.py — AUJI API (with scheduler + Vodafone AutoFill endpoint)
# ==============================================================
from __future__ import annotations

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings
from .db import create_db_and_tables, migrate_jobs_table, get_engine

# Routers
from .routers import auth, jobs, profiles, applications, scrape

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler

# Vodafone services (scraper + autofill)
# NOTE: keep this import path in sync with your project structure.
from app.services.scraper.vodafone import (
    scrape_and_save_vodafone,
    autofill_vodafone_form,  # a.k.a. autofill_vodafone
    Profile as VFProfile,
    Files as VFFiles,
)


# 🚀 App Init
app = FastAPI(title=settings.APP_NAME)


# 🌐 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "*",  # tighten in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 🔌 Routers
app.include_router(auth.router,         prefix="/auth",         tags=["auth"])
app.include_router(jobs.router,         prefix="/jobs",         tags=["jobs"])
app.include_router(profiles.router,     prefix="/profiles",     tags=["profiles"])
app.include_router(applications.router, prefix="/applications", tags=["applications"])
# ملاحظة: هنا بنضيف prefix=/scrape على الراوتر
app.include_router(scrape.router,       prefix="/scrape",       tags=["scrape"])


# 💡 Health Check
@app.get("/health")
def health():
    return {"status": "ok"}


# ======================= Vodafone AutoFill API ======================= #
class ProfileIn(BaseModel):
    title: str = "Mrs."
    preferred_name: str = "S. Elgayah"
    phone_country: str = "Egypt"
    residence_country: str = "Egypt"
    city: str = "Cairo"
    zip_code: str = "11311"
    gender: str = "Female"


class FilesIn(BaseModel):
    cv_path: str = Field(..., description="Absolute path on server")
    cover_letter_path: str | None = None
    portfolio_path: str | None = None
    graduation_path: str | None = None


class ApplyRequest(BaseModel):
    url: str
    profile: ProfileIn | None = None
    files: FilesIn


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw not in ("0", "false", "no", "off")


@app.post("/apply/vodafone")
def apply_vodafone(req: ApplyRequest):
    """Trigger server-side Selenium to autofill Vodafone apply form.
    Returns the result (ok, screenshot_path, ...)."""
    if not req.url or "jobs.vodafone.com" not in req.url:
        raise HTTPException(status_code=400, detail="URL must be a Vodafone apply form")

    # Build dataclasses from validated payload
    profile = VFProfile(**(req.profile.dict() if req.profile else {}))
    files = VFFiles(**req.files.dict())

    # Validate file existence early for clearer errors
    missing: list[str] = [
        p for p in [files.cv_path, files.cover_letter_path, files.portfolio_path, files.graduation_path]
        if p and not os.path.exists(p)
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"File(s) not found on server: {', '.join(missing)}")

    try:
        result = autofill_vodafone_form(
            req.url,
            profile,
            files,
            headless=_env_bool("VODAFONE_HEADLESS", True),
        )
        return result
    except AssertionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ======================= Scheduler (dev toggles) ======================= #

def start_scheduler():
    """Use env vars to control background scraping in dev:
      - ENABLE_SCRAPER_SCHEDULER=0  ➜ disable background scheduler
      - RUN_VODAFONE_ON_STARTUP=0   ➜ skip initial run on startup
      - SCHEDULER_INTERVAL_HOURS=4  ➜ interval hours
    """
    enable_sched = _env_bool("ENABLE_SCRAPER_SCHEDULER", True)
    run_on_start = _env_bool("RUN_VODAFONE_ON_STARTUP", True)
    try:
        every_hours = float(os.getenv("SCHEDULER_INTERVAL_HOURS", "4"))
        if every_hours <= 0:
            every_hours = 4.0
    except Exception:
        every_hours = 4.0

    if not enable_sched:
        print("[SCHED] Disabled (ENABLE_SCRAPER_SCHEDULER=0)")
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(scrape_and_save_vodafone, "interval", hours=every_hours)
    scheduler.start()
    app.state.scheduler = scheduler
    print(f"[SCHED] Started (interval={every_hours}h)")

    if run_on_start:
        try:
            scrape_and_save_vodafone()
            print("[SCHED] Initial Vodafone scrape ran on startup.")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Initial Vodafone scrape failed: {e}")
    else:
        print("[SCHED] Skipped initial Vodafone scrape (RUN_VODAFONE_ON_STARTUP=0)")


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    migrate_jobs_table(get_engine())
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    sch = getattr(app.state, "scheduler", None)
    if sch:
        sch.shutdown(wait=False)
        print("[SCHED] Stopped.")


@app.get("/")
def root():
    return {"message": "AUJI API is running — Auth not yet implemented"}
