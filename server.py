"""
server.py – Amaressence quiz backend

Run:
    pip install -r requirements.txt
    uvicorn server:app --host 0.0.0.0 --port 8000

Admin CSV export:
    GET /api/admin/export?key=<ADMIN_KEY>

Default admin key: amaressence2025
Override with env var:  ADMIN_KEY=mysecret uvicorn server:app ...
"""

import csv
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from io import StringIO

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH   = os.getenv("QUIZ_DB",    "quiz.db")
ADMIN_KEY = os.getenv("ADMIN_KEY",  "amaressence2025")

app = FastAPI(title="Amaressence Quiz API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ── DB ────────────────────────────────────────────────────

def get_db():
    return closing(sqlite3.connect(DB_PATH))


def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                answers     TEXT NOT NULL,
                profile     TEXT NOT NULL,
                reward      TEXT NOT NULL,
                email       TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON responses(created_at)")
        conn.commit()


init_db()


# ── MODELS ───────────────────────────────────────────────

class Submission(BaseModel):
    session_id: str
    answers:    dict[str, str]   # { value_id: color_id }
    profile:    str
    reward:     str
    email:      str


# ── ROUTES ───────────────────────────────────────────────

@app.post("/api/submit")
def submit(data: Submission):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO responses VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                data.session_id,
                datetime.utcnow().isoformat(),
                json.dumps(data.answers, ensure_ascii=False),
                data.profile,
                data.reward,
                data.email,
            ),
        )
        conn.commit()
    return {"ok": True}


@app.get("/api/stats")
def stats():
    """Most popular color per value (public endpoint for community results)."""
    from collections import Counter

    with get_db() as conn:
        rows = conn.execute("SELECT answers FROM responses").fetchall()

    tally: dict[str, Counter] = {}
    for (raw,) in rows:
        try:
            for value_id, color_id in json.loads(raw).items():
                tally.setdefault(value_id, Counter())[color_id] += 1
        except Exception:
            pass

    return {v: dict(c.most_common(5)) for v, c in tally.items()}


@app.get("/api/admin/export")
def export(key: str):
    """Download all responses as CSV. Protected by ADMIN_KEY."""
    if key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, session_id, created_at, answers, profile, reward, email "
            "FROM responses ORDER BY created_at DESC"
        ).fetchall()

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "session_id", "created_at", "answers", "profile", "reward", "email"])
    w.writerows(rows)
    buf.seek(0)

    filename = f"quiz_export_{datetime.utcnow().date()}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Serve the quiz frontend from the same directory
app.mount("/", StaticFiles(directory=".", html=True), name="static")
