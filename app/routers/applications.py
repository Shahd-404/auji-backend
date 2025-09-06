# 🔹 FILE: app/routers/applications.py
# ------------------------------
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from .. import schemas
from ..models import Application
from ..security import get_session  # بنستعمل نفس الـ session helper

router = APIRouter()

# 🗂️ List applications (بسيطة للتجربة)
@router.get("/", response_model=List[schemas.ApplicationOut])
def list_applications(session: Session = Depends(get_session)):
    return session.exec(select(Application).order_by(Application.id.desc())).all()

# ➕ Create application (بدون Auth مؤقتًا)
@router.post("/", response_model=schemas.ApplicationOut)
def create_application(payload: schemas.ApplicationCreate, session: Session = Depends(get_session)):
    app = Application(
        user_id=0,  # TODO: هنبدّلها بالـ user الحقيقي بعد ما نضيف Auth
        job_id=payload.job_id,
        status=payload.status or "pending",
    )
    session.add(app)
    session.commit()
    session.refresh(app)
    return app
