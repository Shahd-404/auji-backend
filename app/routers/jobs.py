# 🔹 FILE: app/routers/jobs.py
# --------------------------------------------------------------
# Jobs Router
# - GET /jobs           → قائمة وظائف مسطّحة مع فلاتر اختيارية (flat)
# - GET /jobs/grouped   → تجميع حسب category (أقسام البروفايلات)
# ✅ يدعم:
#   - profiles=Digital Marketing,Machine Learning
#   - employment_type (قيَم قياسية: "", "freelance", "full_time", "part_time", "internship")
#   ويستخدم SearchProfile لو البارامتر مش مبعوت
#   ولو الاتنين فاضيين → يرجّع كل الأقسام الموجودة (بدون Other).
# --------------------------------------------------------------

from typing import List, Optional, Dict, Any, Set
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select
from ..models import Job, SearchProfile
from ..security import get_session
from ..utils.normalize import norm_employment_type  # ✅ توحيد نوع الوظيفة

router = APIRouter()

# --- Canonical mapping (AR → EN) لضمان التطابق ---
_CANONICAL_NAME = {
    # Digital Marketing
    "Digital Marketing": "Digital Marketing",
    "تسويق رقمي": "Digital Marketing",

    # Data Analysis
    "Data Analysis": "Data Analysis",
    "محلل بيانات": "Data Analysis",

    # Machine Learning
    "Machine Learning": "Machine Learning",
    "مهندس تعلم آلي": "Machine Learning",
    "تعلم آلي": "Machine Learning",
    # في بعض البيانات القديمة كان بيتحط "مهندس برمجيات"
    "مهندس برمجيات": "Machine Learning",
}

def _canon(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip()
    return _CANONICAL_NAME.get(n, n)

def _job_to_dict(j: Job) -> Dict[str, Any]:
    d = j.model_dump() if hasattr(j, "model_dump") else j.dict()
    d.setdefault("apply_url", j.apply_url or j.url)
    d.setdefault("detail_url", j.detail_url)
    d.setdefault("category", j.category)
    # تأكد من إرجاع posted_at كسلسلة ISO لو موجود
    if getattr(j, "posted_at", None) and not isinstance(d.get("posted_at"), str):
        try:
            d["posted_at"] = j.posted_at.isoformat()
        except Exception:
            pass
    return d

def _get_active_categories_from_db(session: Session) -> Set[str]:
    rows = session.exec(
        select(SearchProfile.name).where(SearchProfile.is_active == True)
    ).all()
    active: Set[str] = set()
    for r in rows:
        name = r[0] if isinstance(r, (list, tuple)) else r
        cat = _canon(name)
        if cat:
            active.add(cat)
    return active

def _parse_profiles_param(profiles_param: Optional[str]) -> Set[str]:
    """
    يحوّل profiles= "A,B,C" → {"A","B","C"} بعد التطبيع.
    """
    if not profiles_param:
        return set()
    items = [p.strip() for p in profiles_param.split(",") if p.strip()]
    return { _canon(p) for p in items if _canon(p) }

def _get_all_existing_categories(session: Session) -> Set[str]:
    """
    يرجّع كل الـ categories المميزة الموجودة في جدول الوظائف (بدون None).
    """
    cats = session.exec(select(Job.category).where(Job.category.is_not(None))).all()
    out: Set[str] = set()
    for c in cats:
        val = c[0] if isinstance(c, (list, tuple)) else c
        if val:
            out.add(_canon(val))
    return out

def _apply_employment_source_filter(stmt, employment_type: Optional[str]):
    """
    يربط نوع الوظيفة بالمصدر:
      - freelance  → mostaql فقط
      - full_time  → vodafone فقط
      - قيم أخرى (part_time / internship ...) → فلترة مباشرة على Job.employment_type
      - فاضي/None → لا فلترة بالمصدر
    """
    et = norm_employment_type(employment_type)
    if not et:
        return stmt
    if et == "freelance":
        return stmt.where(Job.source == "mostaql")
    if et == "full_time":
        return stmt.where(Job.source == "vodafone")
    # لقيم أخرى مستقبلًا (part_time/internship)
    return stmt.where(Job.employment_type == et)


@router.get("/")
def list_jobs(
    q: Optional[str] = Query(None, description="نص بحث في العنوان/الشركة/التصنيف"),
    employment_type: Optional[str] = Query(None, description="نوع الوظيفة (full_time / freelance / ...)"),
    experience_level: Optional[str] = None,  # محجوز للمستقبل
    location: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    profiles: Optional[str] = Query(None, description="قائمة بروفايلات EN مفصولة بفواصل لتقييد النتائج"),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """
    يعرض قائمة مسطّحة من الوظائف.
    الأولوية للفلترة:
    1) profiles param لو مبعوت
    2) SearchProfile (المتفعل من الداتابيس)
    3) كل الأقسام الموجودة (fallback)
    """
    try:
        # حدد مجموعات الفلترة
        cats = _parse_profiles_param(profiles)
        if not cats:
            cats = _get_active_categories_from_db(session)
        if not cats:
            cats = _get_all_existing_categories(session)  # fallback

        stmt = select(Job).where(Job.category.in_(cats))

        if q:
            stmt = stmt.where(
                (Job.title.contains(q)) |
                (Job.company.contains(q)) |
                (Job.category.contains(q))
            )
        if location:
            stmt = stmt.where(Job.location.contains(location))

        # ✅ فلترة بالمصدر حسب نوع الوظيفة
        stmt = _apply_employment_source_filter(stmt, employment_type)

        stmt = stmt.order_by(
            Job.posted_at.is_(None),
            Job.posted_at.desc(),
            Job.id.desc(),
        )

        stmt = stmt.offset((page - 1) * limit).limit(limit)
        rows = session.exec(stmt).all()
        return [_job_to_dict(j) for j in rows]
    except Exception as e:
        raise HTTPException(500, f"jobs error: {e}")


@router.get("/grouped")
def list_jobs_grouped(
    session: Session = Depends(get_session),
    q: Optional[str] = Query(None, description="فلترة اختيارية"),
    limit_per_category: int = Query(50, ge=1, le=200, description="أقصى عدد عناصر لكل قسم"),
    profiles: Optional[str] = Query(None, description="قائمة بروفايلات EN مفصولة بفواصل لعرض أقسام محددة فقط"),
    employment_type: Optional[str] = Query(None, description="نوع الوظيفة (full_time / freelance / ...)"),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    يرجّع الوظائف مجمّعة حسب الـ category (EN).
    الأولوية للفلترة:
    1) profiles param لو مبعوت
    2) SearchProfile (المتفعل من الداتابيس)
    3) كل الأقسام الموجودة (fallback)
    لا يتم إنشاء سكشن "Other".
    """
    try:
        cats = _parse_profiles_param(profiles)
        if not cats:
            cats = _get_active_categories_from_db(session)
        if not cats:
            cats = _get_all_existing_categories(session)

        stmt = select(Job).where(
            Job.category.in_(cats),
            Job.category.is_not(None)
        )

        if q:
            stmt = stmt.where(
                (Job.title.contains(q)) |
                (Job.company.contains(q)) |
                (Job.category.contains(q))
            )

        # ✅ فلترة بالمصدر حسب نوع الوظيفة
        stmt = _apply_employment_source_filter(stmt, employment_type)

        stmt = stmt.order_by(
            Job.posted_at.is_(None),
            Job.posted_at.desc(),
            Job.id.desc(),
        )
        rows = session.exec(stmt).all()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for j in rows:
            cat = _canon(j.category)
            if not cat:
                continue  # لا Other
            bucket = grouped.setdefault(cat, [])
            if len(bucket) < limit_per_category:
                bucket.append(_job_to_dict(j))

        return grouped
    except Exception as e:
        raise HTTPException(500, f"grouped error: {e}")

# python -m venv .venv
#.\.venv\Scripts\activate
# pip install -r requirements.txt
# pip install "uvicorn[standard]" fastapi
# python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# python -m playwright install chromium
