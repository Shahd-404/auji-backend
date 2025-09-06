# 🔹 FILE: app/routers/auth.py
# ----------------------------
# 🧩 Imports
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from .. import schemas
from ..models import User
from ..security import hash_password, verify_password, create_access_token, get_current_user
from ..db import get_session
from ..config import settings

router = APIRouter()

# 📝 Register
@router.post("/register", response_model=schemas.UserOut)
def register(payload: schemas.RegisterIn, session: Session = Depends(get_session)):
    exists = session.exec(select(User).where(User.email == payload.email)).first()
    if exists:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=payload.email, hashed_password=hash_password(payload.password), full_name=payload.full_name)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

# 🔐 Login → JWT
@router.post("/login", response_model=schemas.TokenOut)
def login(payload: schemas.LoginIn, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token({"sub": user.email}, settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return schemas.TokenOut(access_token=token)

# 👤 Me
@router.get("/me", response_model=schemas.UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user