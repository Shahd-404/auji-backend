# 🔹 FILE: app/routers/profiles.py
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from .. import schemas
from ..models import Profile
from ..security import get_current_user, get_session

router = APIRouter()

@router.get("/", response_model=List[schemas.ProfileOut])
def list_profiles(session: Session = Depends(get_session), user=Depends(get_current_user)):
    return session.exec(select(Profile).where(Profile.user_id == user.id)).all()

@router.post("/", response_model=schemas.ProfileOut)
def create_profile(payload: schemas.ProfileCreate, session: Session = Depends(get_session), user=Depends(get_current_user)):
    profile = Profile(user_id=user.id, title=payload.title, active=True)
    session.add(profile); session.commit(); session.refresh(profile)
    return profile

@router.patch("/{profile_id}/toggle", response_model=schemas.ProfileOut)
def toggle_profile(profile_id: int, session: Session = Depends(get_session), user=Depends(get_current_user)):
    profile = session.get(Profile, profile_id)
    if not profile or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.active = not profile.active
    session.add(profile); session.commit(); session.refresh(profile)
    return profile
