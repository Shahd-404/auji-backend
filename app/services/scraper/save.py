# 🔹 FILE: app/services/scraper/save.py
# ==============================================================
# AUJI – Save Jobs Helpers (SQLModel)
# - Upsert (no mass-delete): يعتمد على detail_url / apply_url / url + source
# - يحافظ على الوظائف القديمة ويحدّث الموجودة بدل ما يمسح الكل
# - متوافق مع الكود القديم: نفس التوقيع save_jobs(items, session)
# - مضاف: save_jobs_bulk(items) لراحة الاستخدام بدون تمرير Session
# ==============================================================

from typing import List, Any, Optional
from sqlmodel import Session, select
from app.models import Job
from app.db import get_session


def _find_existing_job(session: Session, item: dict) -> Optional[Job]:
    """
    يحاول إيجاد سجل موجود بناءً على:
    1) detail_url
    2) apply_url
    3) url (للتوافق مع الكود القديم)
    + نفس المصدر (لو متاح)
    """
    source = item.get("source")
    detail_url = item.get("detail_url")
    apply_url = item.get("apply_url")
    legacy_url = item.get("url")

    # بالأولوية: detail_url
    if detail_url:
        stmt = select(Job).where(Job.detail_url == detail_url)
        if source:
            stmt = stmt.where(Job.source == source)
        found = session.exec(stmt).first()
        if found:
            return found

    # بعده: apply_url
    if apply_url:
        stmt = select(Job).where(Job.apply_url == apply_url)
        if source:
            stmt = stmt.where(Job.source == source)
        found = session.exec(stmt).first()
        if found:
            return found

    # أخيرًا: legacy url
    if legacy_url:
        stmt = select(Job).where(Job.url == legacy_url)
        if source:
            stmt = stmt.where(Job.source == source)
        found = session.exec(stmt).first()
        if found:
            return found

    return None


def _update_job_fields(dest: Job, src: dict) -> None:
    """
    يحدّث الحقول الآتية فقط إن كانت قيمة جديدة متوفرة:
    """
    updatable = [
        "title",
        "company",
        "location",
        "description",
        "detail_url",
        "apply_url",
        "url",           # للتوافق مع الكود القديم
        "source",
        "category",
        "posted_at",
    ]
    for key in updatable:
        val = src.get(key)
        if val not in (None, ""):
            setattr(dest, key, val)


def save_jobs(items: List[Any], session: Session) -> int:
    """
    ✅ سلوك جديد: Upsert بدون مسح شامل
    - يتجاهل العناصر غير الصحيحة
    - يدرج سجلات جديدة ويحدّث الموجودة
    - يرجّع عدد السجلات المتأثرة (مضافة + محدّثة)
    """
    if not items:
        return 0

    affected = 0

    for raw in items:
        if not isinstance(raw, dict):
            # لو لأي سبب عنصر مش dict، تجاهله
            continue

        # ضمان وجود source (افتراضيًا vodafone لتوحيد المصدر)
        if not raw.get("source"):
            raw["source"] = "vodafone"

        # لازم يبقى فيه واحدة من الروابط لتعريف السجل
        if not (raw.get("detail_url") or raw.get("apply_url") or raw.get("url")):
            continue

        exists = _find_existing_job(session, raw)
        if exists:
            _update_job_fields(exists, raw)
            affected += 1
        else:
            session.add(Job(**raw))
            affected += 1

    session.commit()
    return affected


# 🆕 راحة استخدام: نفس المنطق لكن بيدير الـ Session داخليًا
def save_jobs_bulk(items: List[Any]) -> int:
    """
    Wrapper مريح: يفتح Session داخليًا وينادي save_jobs(items, session)
    """
    with next(get_session()) as db:
        return save_jobs(items, db)
