# --------------------------------------------------------------
# AUJI – Data Models (SQLModel)
# - User accounts & profiles
# - Search profiles (toggles) to drive dynamic scraping
# - Jobs (with category + detail/apply URLs)
# - Applications (user ↔ job)
# --------------------------------------------------------------

from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


# 👤 User Model
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    hashed_password: str
    full_name: Optional[str] = None
    is_active: bool = Field(default=True, index=True)
    role: str = Field(default="user")


# 📝 User Profile (Bio/Skills/Experience for the user)
class Profile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    bio: Optional[str] = None
    skills: Optional[str] = None
    experience: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# 🎯 Search Profile (توجّهات البحث)
class SearchProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)               # اسم البروفايل (AR/EN)
    query: Optional[str] = None                 # اختياري
    locations: Optional[str] = None             # CSV/JSON نصي اختياري
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# 💼 Job Model
class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    # أساسي
    title: str = Field(index=True)
    company: str = Field(default="Vodafone", index=True)
    location: Optional[str] = Field(default=None, index=True)

    # 🔗 الروابط
    # (url) للتوافق مع الكود القديم – يُفضَّل استخدام detail_url/apply_url
    url: Optional[str] = None
    detail_url: Optional[str] = Field(default=None, index=True)
    apply_url: Optional[str] = None

    # 🗂️ التصنيف (Category = اسم البروفايل EN)
    category: Optional[str] = Field(default=None, index=True)

    # المصدر + التواريخ
    source: str = Field(default="vodafone", index=True)
    posted_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# 📄 Application Model
class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    status: str = Field(default="pending", index=True)  # pending / applied / rejected / hired ...
    applied_at: datetime = Field(default_factory=datetime.utcnow)
