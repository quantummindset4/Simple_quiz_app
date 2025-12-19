import os, uuid, datetime
from typing import Any, Dict, Optional, Tuple, List

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sqlalchemy import (
    create_engine, text, Column, String, Text, Integer, DateTime
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import ProgrammingError

# ---------- ENV ----------
DB_URL = os.getenv("DB_URL", "postgresql+psycopg2://quiz:change-me@db:5432/quizdb")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")

# ---------- DB ----------
engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

class Quiz(Base):
    __tablename__ = "quizzes"
    id = Column(String, primary_key=True)
    test_name = Column(Text, nullable=False)
    skill = Column(String(120), nullable=True, index=True)
    difficulty = Column(String(60), nullable=True, index=True)
    yoe = Column(String(60), nullable=True)
    question_count = Column(Integer, nullable=False)
    json = Column(JSONB, nullable=False)
    status = Column(String(20), nullable=False, default="published", index=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_quizzes_json ON quizzes USING GIN (json)"
            ))
        except ProgrammingError:
            pass

# ---------- APP ----------
app = FastAPI(title="QM Home Quiz API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ALLOW_ORIGINS.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve static UI from ./static (index.html)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ---------- Validation identical to frontend ----------
def normalize_and_validate(root: Any):
    arr = root if isinstance(root, list) else (root.get("questions") if isinstance(root, dict) else None)
    if not arr or not isinstance(arr, list):
        raise HTTPException(400, 'Invalid JSON: must be an array or {"questions": [...]}')
    items = []
    for i, it in enumerate(arr, start=1):
        if not isinstance(it, dict):
            raise HTTPException(400, f"Q{i}: not an object")
        q = (it.get("q") or it.get("question") or "").strip()
        if not q:
            raise HTTPException(400, f"Q{i}: missing question text")
        if isinstance(it.get("options"), list):
            options = ["" if x is None else str(x) for x in it["options"]]
        else:
            options = [it.get("1"), it.get("2"), it.get("3"), it.get("4")]
            options = ["" if x is None else str(x) for x in options]
        if len(options) != 4:
            raise HTTPException(400, f"Q{i}: must provide exactly 4 options")
        if any(not o or not o.strip() for o in options):
            raise HTTPException(400, f"Q{i}: options cannot be empty")
        a = it.get("a", None)
        if a is None or a == "":
            raise HTTPException(400, f"Q{i}: missing correct answer (a)")
        try:
            ans_idx = int(a)
        except:
            try:
                ans_idx = int(str(a).strip())
            except:
                raise HTTPException(400, f"Q{i}: invalid answer index")
        if 1 <= ans_idx <= 4:
            ans_idx -= 1
        if not (0 <= ans_idx < 4):
            raise HTTPException(400, f"Q{i}: answer (a) must be 1–4 (or 0–3)")
        items.append({"q": q, "options": options, "correct": ans_idx})

    test_name = ""
    if isinstance(root, dict):
        test_name = root.get("Test Name") or root.get("testName") \
                    or (root.get("meta", {}) or {}).get("testName") or ""
    return items, test_name

# ---------- Models ----------
class ImportPayload(BaseModel):
    json: dict | list
    skill: Optional[str] = None
    difficulty: Optional[str] = None
    yoe: Optional[str] = None

def require_admin(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.split(" ", 1)[1] != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")

# ---------- Routes ----------
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def home():
    index_path = os.path.join(static_dir, "index.html")
    return FileResponse(index_path)

@app.post("/api/quiz/validate")
def api_validate(payload: ImportPayload):
    items, test_name = normalize_and_validate(payload.json)
    return {"ok": True, "question_count": len(items), "detected_test_name": test_name or None}

@app.post("/api/quiz/import")
def api_import(payload: ImportPayload, authorization: Optional[str] = Header(None)):
    require_admin(authorization)
    items, test_name = normalize_and_validate(payload.json)
    count = len(items)
    now = datetime.datetime.utcnow()
    skill = (payload.skill or "").strip() or (test_name.split()[0] if test_name else None) or "General"
    difficulty = (payload.difficulty or "Mixed").strip()
    yoe = (payload.yoe or "").strip() or "N/A"
    display_name = test_name or f"Custom {skill}"

    qid = str(uuid.uuid4())
    row = Quiz(
        id=qid,
        test_name=display_name,
        skill=skill,
        difficulty=difficulty,
        yoe=yoe,
        question_count=count,
        json=payload.json,
        status="published",
        created_at=now,
        updated_at=now,
    )
    with Session.begin() as s:
        s.add(row)

    return {
        "ok": True,
        "id": qid,
        "url": f"{PUBLIC_BASE_URL}/quizzes/{qid}.json",
        "meta": {"test_name": display_name, "skill": skill, "difficulty": difficulty, "yoe": yoe, "count": count}
    }

@app.get("/api/quizzes")
def list_quizzes(skill: Optional[str] = None, difficulty: Optional[str] = None, search: Optional[str] = None):
    sql = "SELECT id, test_name, skill, difficulty, yoe, question_count, created_at FROM quizzes WHERE status='published'"
    params: Dict[str, Any] = {}
    if skill:
        sql += " AND skill = :skill"; params["skill"] = skill
    if difficulty:
        sql += " AND difficulty = :difficulty"; params["difficulty"] = difficulty
    if search:
        sql += " AND (test_name ILIKE :q OR skill ILIKE :q)"; params["q"] = f"%{search}%"
    sql += " ORDER BY skill ASC, created_at DESC"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        summary = conn.execute(text(
            "SELECT skill, COUNT(*) AS n FROM quizzes WHERE status='published' GROUP BY skill ORDER BY skill"
        )).mappings().all()

    items = [{
        "id": r["id"], "test_name": r["test_name"], "skill": r["skill"], "difficulty": r["difficulty"],
        "yoe": r["yoe"], "count": r["question_count"],
        "url": f"{PUBLIC_BASE_URL}/quizzes/{r['id']}.json",
        "created_at": r["created_at"].isoformat() + "Z",
    } for r in rows]
    skills = [{"skill": s["skill"], "count": s["n"]} for s in summary]
    return {"skills": skills, "items": items}

@app.get("/manifest.json")
def manifest():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id FROM quizzes WHERE status='published' ORDER BY created_at DESC LIMIT 500"
        )).all()
    quizzes = [{"url": f"{PUBLIC_BASE_URL}/quizzes/{r[0]}.json"} for r in rows]
    return {"quizzes": quizzes}

@app.get("/quizzes/{quiz_id}.json")
def get_quiz_json(quiz_id: str):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT json FROM quizzes WHERE id=:id AND status='published'"),
                           {"id": quiz_id}).scalar()
    if row is None:
        raise HTTPException(404, "Quiz not found")
    return JSONResponse(content=row)

# Boot
init_db()
