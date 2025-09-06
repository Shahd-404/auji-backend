# 🔹 FILE: app/schemas.py
# -----------------------
# 🧩 Imports
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, EmailStr

# 🧾 Auth
class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    role: str

    class Config:
        from_attributes = True

# 💼 Jobs
class JobCreate(BaseModel):
    title: str
    company: str
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None

class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: Optional[str]
    url: Optional[str]
    description: Optional[str]
    source: Optional[str]
    posted_at: Optional[datetime]

    class Config:
        from_attributes = True

# 👤 Profiles
class ProfileCreate(BaseModel):
    title: str

class ProfileOut(BaseModel):
    id: int
    title: str
    active: bool

    class Config:
        from_attributes = True

# 📨 Applications
class ApplicationCreate(BaseModel):
    job_id: int
    profile_id: int

class ApplicationOut(BaseModel):
    id: int
    status: str
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True
