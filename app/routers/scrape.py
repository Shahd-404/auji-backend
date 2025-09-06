# FILE: app/routers/scrape.py
# ==============================================================
# AUJI – Scrape Router (Vodafone + Mostaql)
#  - Vodafone: تشغيل بالبروفايلات / أو برابط مباشر / Autofill / Enrich details
#  - Mostaql:  تشغيل بالبروفايلات أو بمزيج categories مباشرة
# ==============================================================

from __future__ import annotations

from typing import Optional, List, Literal, Union, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from sqlmodel import Session

from ..security import get_session

from ..services.scraper.vodafone import (
    fetch_vodafone_jobs,
    scrape_and_save_vodafone_for_profiles,
    autofill_vodafone_from_db,
    autofill_vodafone_form,
    AutofillConfig,
    enrich_vodafone_descriptions,
)

from ..services.scraper.mostaql import (
    scrape_mostaql_for_profiles,
    scrape_mostaql,
)

from ..services.scraper.save import save_jobs


router = APIRouter(tags=["scrape"])


# ===================== Payloads ===============================

class VodafoneIn(BaseModel):
    url: HttpUrl
    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://jobs.vodafone.com/careers?domain=vodafone.com&query=Digital+Marketing&start=0&pid=563018675569773&sort_by=solr"
            }
        }

class ProfilesIn(BaseModel):
    active_profiles: Optional[List[str]] = None
    # للتوافق القديم
    max_pages: Optional[int] = 1
    per_page: Optional[int] = 10
    class Config:
        json_schema_extra = {
            "example": {
                "active_profiles": ["Machine Learning", "Digital Marketing", "Data Analysis"],
                "max_pages": 1,
                "per_page": 10,
            }
        }

class MostaqlProfilesIn(BaseModel):
    active_profiles: Optional[List[str]] = None
    pages: Optional[int] = 2
    class Config:
        json_schema_extra = {
            "example": {"active_profiles": ["Digital Marketing", "Machine Learning"], "pages": 2}
        }

class MostaqlDirectIn(BaseModel):
    categories: str
    pages: Optional[int] = 2
    class Config:
        json_schema_extra = {"example": {"categories": "development,marketing", "pages": 2}}

class VodafoneAutofillIn(BaseModel):
    limit: int = 5
    category: Optional[str] = None
    headless: bool = True
    background: bool = True
    class Config:
        json_schema_extra = {
            "example": {"limit": 5, "category": "Machine Learning", "headless": True, "background": True}
        }

# ===== Apply one URL (FE calls /apply/vodafone) =====
class ApplyVodafoneFiles(BaseModel):
    cv_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    portfolio_path: Optional[str] = None
    graduation_path: Optional[str] = None

class ApplyVodafoneProfile(BaseModel):
    title: Optional[str] = None
    preferred_name: Optional[str] = None
    phone_country: Optional[str] = None
    residence_country: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    gender: Optional[str] = None

class ApplyVodafoneIn(BaseModel):
    url: HttpUrl
    headless: bool = True
    background: bool = True
    keep_open: Optional[bool] = None
    keep_open_seconds: Optional[int] = None
    profile: Optional[ApplyVodafoneProfile] = None
    files: Optional[ApplyVodafoneFiles] = None
    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://jobs.vodafone.com/careers/apply?pid=563018686752862&domain=vodafone.com",
                "headless": False,
                "background": False,
                "keep_open": True,
                "keep_open_seconds": 120,
                "profile": {
                    "title": "Mrs.",
                    "preferred_name": "S. Elgayah",
                    "phone_country": "Egypt",
                    "residence_country": "Egypt",
                    "city": "Cairo",
                    "zip_code": "11311",
                    "gender": "Female"
                },
                "files": {
                    "cv_path": "/srv/files/Shahd_Alaa.pdf",
                    "cover_letter_path": "/srv/files/letter.pdf",
                    "portfolio_path": "/srv/files/portfolio.pdf",
                    "graduation_path": "/srv/files/graduation.pdf"
                }
            }
        }

class StartedResponse(BaseModel):
    status: Literal["started"]
    limit: int
    category: Optional[str] = None

class OkResponse(BaseModel):
    status: Literal["ok"]
    applied: int

ResponseAutofill = Union[StartedResponse, OkResponse]


# ===================== Helpers ===============================

def _map_apply_payload_to_config(p: ApplyVodafoneIn) -> AutofillConfig:
    cfg = AutofillConfig()
    # files
    if p.files:
        if p.files.cv_path:
            cfg.cv = p.files.cv_path
        if p.files.cover_letter_path:
            cfg.attachment1 = p.files.cover_letter_path
        if p.files.portfolio_path:
            cfg.attachment2 = p.files.portfolio_path
        if p.files.graduation_path:
            cfg.attachment3 = p.files.graduation_path
    # profile
    if p.profile:
        if p.profile.title:
            cfg.title = p.profile.title
        if p.profile.preferred_name:
            cfg.preferred_name = p.profile.preferred_name
        if p.profile.residence_country:
            cfg.country_of_residence = p.profile.residence_country
        if p.profile.city:
            cfg.city = p.profile.city
        if p.profile.zip_code:
            cfg.zip_code = p.profile.zip_code
        if p.profile.gender:
            cfg.gender = p.profile.gender
        if p.profile.phone_country:
            s = p.profile.phone_country.strip().lower()
            cfg.phone_country_query = (s[:3] if len(s) >= 3 else s) or cfg.phone_country_query
            if s.startswith("egy"):
                cfg.phone_country_query = "egy"
    # keep-open flags
    if p.keep_open is not None:
        cfg.keep_open = p.keep_open
    if p.keep_open_seconds is not None:
        cfg.keep_open_seconds = p.keep_open_seconds
    return cfg


# ===================== Vodafone ===============================

@router.post("/apply/vodafone", summary="Vodafone: Autofill مباشرة على رابط Apply واحد")
def apply_vodafone_one(payload: ApplyVodafoneIn, tasks: BackgroundTasks) -> Dict[str, Any]:
    try:
        cfg = _map_apply_payload_to_config(payload)
        if payload.background:
            tasks.add_task(
                autofill_vodafone_form,
                str(payload.url),
                payload.headless,
                cfg,
                keep_open=cfg.keep_open,
                keep_open_seconds=cfg.keep_open_seconds,
            )
            return {"ok": True, "started": True}
        ok = autofill_vodafone_form(
            str(payload.url),
            headless=payload.headless,
            config=cfg,
            keep_open=cfg.keep_open,
            keep_open_seconds=cfg.keep_open_seconds,
        )
        return {"ok": bool(ok)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/vodafone/run", summary="Vodafone: تشغيل بالبروفايلات (من DB أو من Body)")
def run_vodafone(payload: Optional[ProfilesIn] = None):
    try:
        names = payload.active_profiles if payload else None
        pages = (payload.max_pages or 1) if payload else 1
        saved = scrape_and_save_vodafone_for_profiles(names, pages=pages)
        return {
            "status": "ok",
            "profiles": names or "active-from-db",
            "pages": pages,
            "per_page": (payload.per_page if payload else 10),
            "saved_or_updated": saved,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vodafone/run-url", summary="Vodafone: تشغيل برابط نتائج محدد")
def run_vodafone_by_url(payload: VodafoneIn, session: Session = Depends(get_session)):
    try:
        jobs = fetch_vodafone_jobs(str(payload.url))
        saved = save_jobs(jobs, session)
        return {"status": "ok", "url": str(payload.url), "fetched": len(jobs), "saved": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vodafone/run-profiles", summary="Vodafone: تشغيل بقائمة بروفايلات محددة (مرادف لـ /vodafone/run)")
def run_vodafone_by_profiles(payload: ProfilesIn):
    try:
        pages = payload.max_pages or 1
        saved = scrape_and_save_vodafone_for_profiles(payload.active_profiles, pages=pages)
        return {
            "status": "ok",
            "profiles": payload.active_profiles,
            "pages": pages,
            "per_page": payload.per_page or 10,
            "saved_or_updated": saved,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vodafone/autofill", summary="Vodafone: تشغيل الملء التلقائي من قاعدة البيانات", response_model=ResponseAutofill)
def run_vodafone_autofill(payload: VodafoneAutofillIn, tasks: BackgroundTasks):
    try:
        if payload.background:
            tasks.add_task(
                autofill_vodafone_from_db,
                limit=payload.limit,
                category=payload.category,
                headless=payload.headless,
            )
            return {"status": "started", "limit": payload.limit, "category": payload.category}
        n = autofill_vodafone_from_db(limit=payload.limit, category=payload.category, headless=payload.headless)
        return {"status": "ok", "applied": n}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vodafone/enrich-details", summary="Vodafone: ملء الوصف/المتطلبات للوظائف القديمة (اختياري)")
def vodafone_enrich_details(limit: int = 50):
    try:
        n = enrich_vodafone_descriptions(limit=limit)
        return {"status": "ok", "enriched": n}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===================== Mostaql ===============================

@router.post("/mostaql/run", summary="Mostaql: تشغيل بناءً على البروفايلات المفعّلة")
def run_mostaql(payload: MostaqlProfilesIn, session: Session = Depends(get_session)):
    try:
        jobs = scrape_mostaql_for_profiles(payload.active_profiles or [], pages=payload.pages or 2)
        saved = save_jobs(jobs, session)
        return {
            "status": "ok",
            "profiles": payload.active_profiles or [],
            "pages": payload.pages or 2,
            "fetched": len(jobs),
            "saved": saved,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mostaql/run-direct", summary="Mostaql: تشغيل مباشر بمزيج categories (marketing | development | ...)")
def run_mostaql_direct(payload: MostaqlDirectIn, session: Session = Depends(get_session)):
    try:
        jobs = scrape_mostaql(payload.categories, pages=payload.pages or 2)
        saved = save_jobs(jobs, session)
        return {
            "status": "ok",
            "categories": payload.categories,
            "pages": payload.pages or 2,
            "fetched": len(jobs),
            "saved": saved,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
