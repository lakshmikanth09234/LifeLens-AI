import sqlite3
import json
import uuid
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lifelens.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                image_mime TEXT,
                thumbnail_b64 TEXT,
                language TEXT NOT NULL,
                user_question TEXT,
                category TEXT,
                category_display TEXT,
                summary TEXT,
                urgency_level TEXT,
                structured_data TEXT NOT NULL,
                grounding_data TEXT,
                is_grounded INTEGER DEFAULT 0,
                is_favorite INTEGER DEFAULT 0
            )
        """)
        # Ensure columns exist for existing tables
        try:
            conn.execute("ALTER TABLE analyses ADD COLUMN is_favorite INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE analyses ADD COLUMN original_structured_data TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyses_session_created 
            ON analyses(session_id, created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyses_id_session 
            ON analyses(id, session_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS follow_ups (
                id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_followups_analysis 
            ON follow_ups(analysis_id, created_at ASC)
        """)


def save_analysis(session_id, thumbnail_b64, image_mime, language, user_question, 
                  category, category_display, summary, urgency_level, 
                  structured_data, grounding_data=None, is_grounded=0):
    analysis_id = str(uuid.uuid4())
    json_data = json.dumps(structured_data, ensure_ascii=False)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO analyses (
                id, session_id, thumbnail_b64, image_mime, language, user_question,
                category, category_display, summary, urgency_level,
                structured_data, original_structured_data, grounding_data, is_grounded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            analysis_id,
            session_id,
            thumbnail_b64,
            image_mime,
            language,
            user_question or "",
            category,
            category_display,
            summary,
            urgency_level,
            json_data,
            json_data,
            json.dumps(grounding_data or [], ensure_ascii=False),
            1 if is_grounded else 0
        ))
    return analysis_id


def get_history(session_id, limit=50, favorites_only=False, category=None, search=None):
    with get_db() as conn:
        query = """
            SELECT id, created_at, thumbnail_b64, language, user_question,
                   category, category_display, summary, urgency_level, is_grounded, is_favorite, structured_data
            FROM analyses
            WHERE session_id = ?
        """
        params = [session_id]
        if favorites_only:
            query += " AND is_favorite = 1"
        if category and category != "all":
            query += " AND (category LIKE ? OR category_display LIKE ?)"
            params.extend([f"{category}%", f"{category}%"])
        if search and search.strip():
            s = f"%{search.strip()}%"
            query += " AND (summary LIKE ? OR category_display LIKE ? OR user_question LIKE ?)"
            params.extend([s, s, s])
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["structured_data"] = json.loads(d.get("structured_data") or "{}")
            except Exception:
                d["structured_data"] = {}
            d["is_favorite"] = bool(d.get("is_favorite", 0))
            result.append(d)
        return result


def get_analysis(analysis_id, session_id):
    """Strictly enforces session_id ownership."""
    if not analysis_id or not session_id:
        return None
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT * FROM analyses WHERE id = ? AND session_id = ?",
            (analysis_id, session_id)
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        result = dict(row)
        result["structured_data"] = json.loads(result["structured_data"])
        if result.get("original_structured_data"):
            try:
                result["original_structured_data"] = json.loads(result["original_structured_data"])
            except Exception:
                result["original_structured_data"] = result["structured_data"]
        else:
            result["original_structured_data"] = result["structured_data"]
        result["grounding_data"] = json.loads(result["grounding_data"]) if result["grounding_data"] else []
        result["is_favorite"] = bool(result.get("is_favorite", 0))
        
        follow_cursor = conn.execute("""
            SELECT role, message, created_at
            FROM follow_ups
            WHERE analysis_id = ?
            ORDER BY created_at ASC
        """, (analysis_id,))
        result["follow_ups"] = [dict(f) for f in follow_cursor.fetchall()]
        
        return result


def toggle_favorite(analysis_id, session_id):
    """Toggles is_favorite for a session-owned analysis."""
    if not analysis_id or not session_id:
        return None
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT is_favorite FROM analyses WHERE id = ? AND session_id = ?",
            (analysis_id, session_id)
        )
        row = cursor.fetchone()
        if not row:
            return None
        new_val = 0 if row["is_favorite"] else 1
        conn.execute(
            "UPDATE analyses SET is_favorite = ? WHERE id = ? AND session_id = ?",
            (new_val, analysis_id, session_id)
        )
        return bool(new_val)


def update_analysis_translation(analysis_id, session_id, language, structured_data):
    """Updates an analysis with translated content and target language."""
    if not analysis_id or not session_id:
        return False
    with get_db() as conn:
        conn.execute("""
            UPDATE analyses 
            SET language = ?,
                category_display = ?,
                summary = ?,
                structured_data = ?
            WHERE id = ? AND session_id = ?
        """, (
            language,
            structured_data.get("category_display_name", ""),
            structured_data.get("summary", ""),
            json.dumps(structured_data, ensure_ascii=False),
            analysis_id,
            session_id
        ))
        return True


def delete_analysis(analysis_id, session_id):
    """Strictly deletes only if session_id matches."""
    if not analysis_id or not session_id:
        return False
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM analyses WHERE id = ? AND session_id = ?",
            (analysis_id, session_id)
        )
        if cursor.rowcount > 0:
            conn.execute("DELETE FROM follow_ups WHERE analysis_id = ?", (analysis_id,))
            return True
        return False


def clear_history(session_id):
    if not session_id:
        return 0
    with get_db() as conn:
        cursor = conn.execute("SELECT id FROM analyses WHERE session_id = ?", (session_id,))
        ids = [row["id"] for row in cursor.fetchall()]
        if ids:
            conn.execute(f"DELETE FROM follow_ups WHERE analysis_id IN ({','.join(['?']*len(ids))})", ids)
            conn.execute("DELETE FROM analyses WHERE session_id = ?", (session_id,))
        return len(ids)


def save_follow_up(analysis_id, session_id, role, message):
    """Saves follow-up only after verifying session ownership."""
    with get_db() as conn:
        # Verify ownership
        check = conn.execute(
            "SELECT id FROM analyses WHERE id = ? AND session_id = ?",
            (analysis_id, session_id)
        ).fetchone()
        if not check:
            raise PermissionError("Analysis does not belong to this session.")

        follow_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO follow_ups (id, analysis_id, role, message)
            VALUES (?, ?, ?, ?)
        """, (follow_id, analysis_id, role, message))
        return follow_id
