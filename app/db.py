# 🔹 FILE: app/db.py
# ==============================================================
# AUJI – DB bootstrap + lightweight migrations for SQLite
# - create_db_and_tables(): creates tables from SQLModel metadata
# - migrate_jobs_table(): adds missing columns if schema changed
# - create_job_indexes(): speed up common queries
# - get_session(): FastAPI dependency
# - get_engine(): expose engine when needed
# ==============================================================

from typing import Generator, Set
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings

# 🛠 إعداد قاعدة البيانات
engine = create_engine(settings.DATABASE_URL, echo=False, future=True)

# 🆕 SessionLocal للتوافق
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
)

def get_engine():
    return engine

# 🗄 إنشاء الجداول
def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)

# ========= Helpers =========
def _table_columns(engine, table_name: str) -> Set[str]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        # كل Row: (cid, name, type, notnull, dflt_value, pk)
        return {r[1] for r in rows}

def _safe_add_column(engine, table: str, column_ddl: str):
    """
    column_ddl مثال: "detail_url TEXT"
    يضيف العمود فقط لو كان غير موجود (SQLite).
    """
    col_name = column_ddl.split()[0]
    existing = _table_columns(engine, table)
    if col_name not in existing:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column_ddl};")

# ========= Lightweight Migrations (SQLite) =========
def migrate_jobs_table(engine) -> None:
    """
    يضيف الأعمدة الجديدة لجدول job لو ناقصة — بدون فقد بيانات.
    استدعِها بعد create_db_and_tables().
    """
    try:
        cols = _table_columns(engine, "job")
    except Exception:
        # لو الجدول مش موجود لسه
        return

    if not cols:
        return

    # أعمدة نضمن وجودها (متوافقة مع الموديل Job)
    _safe_add_column(engine, "job", "detail_url TEXT")
    _safe_add_column(engine, "job", "apply_url TEXT")
    _safe_add_column(engine, "job", "category TEXT")
    _safe_add_column(engine, "job", "source TEXT")
    _safe_add_column(engine, "job", "employment_type TEXT")  # للفلاتر
    _safe_add_column(engine, "job", "posted_at TIMESTAMP")
    _safe_add_column(engine, "job", "created_at TIMESTAMP")
    _safe_add_column(engine, "job", "updated_at TIMESTAMP")
    # ✅ نخزن نص المتطلبات/الوصف الذي سيظهر في "اعرف التفاصيل"
    _safe_add_column(engine, "job", "description TEXT")
    # عمود url قديم للتوافق — لا نلمسه لو موجود بالفعل

# ========= Indexes (سرعة) =========
def create_job_indexes(engine):
    """
    فهارس لسرعة الاستعلام (WHERE/ORDER BY):
      - category / source / employment_type / posted_at / company / title
      - detail_url / apply_url (لتسريع upsert/exists)
    """
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_job_category        ON job (category)",
        "CREATE INDEX IF NOT EXISTS ix_job_source          ON job (source)",
        "CREATE INDEX IF NOT EXISTS ix_job_employment_type ON job (employment_type)",
        "CREATE INDEX IF NOT EXISTS ix_job_posted_at       ON job (posted_at)",
        "CREATE INDEX IF NOT EXISTS ix_job_company         ON job (company)",
        "CREATE INDEX IF NOT EXISTS ix_job_title           ON job (title)",
        "CREATE INDEX IF NOT EXISTS ix_job_detail_url      ON job (detail_url)",
        "CREATE INDEX IF NOT EXISTS ix_job_apply_url       ON job (apply_url)",
    ]
    with engine.begin() as conn:
        for s in stmts:
            try:
                conn.exec_driver_sql(s)
            except Exception as e:
                print(f"[DB] index failed: {s} -> {e}")

# 🔌 Dependency - Session
def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yields a SQLModel Session.
    Example:
        @router.get("/")
        def read_items(session: Session = Depends(get_session)):
            ...
    """
    with SessionLocal() as session:
        yield session
