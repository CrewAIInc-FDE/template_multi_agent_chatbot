import os
import sqlite3
import uuid

DB_PATH = os.path.join(os.path.dirname(__file__), "chatbot.db")

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS channels (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        last_state_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL REFERENCES channels(id),
        role TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        event_type TEXT,
        image_base64 TEXT,
        timestamp TEXT DEFAULT (datetime('now')),
        event_id TEXT UNIQUE,
        agent_role TEXT,
        thinking_content TEXT,
        tools_used TEXT,
        timeline TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_messages_channel
        ON messages(channel_id, timestamp);

    CREATE INDEX IF NOT EXISTS idx_channels_conversation
        ON channels(conversation_id);
"""


def _ensure_schema(conn):
    """Create the schema if it's missing.

    Runs on every connection so the app self-heals even if the database
    file is removed or recreated empty while the server is running.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='channels'"
    ).fetchone()
    if not exists:
        conn.executescript(_SCHEMA)
        conn.commit()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def init_db():
    conn = _get_conn()
    conn.executescript(_SCHEMA)
    for table, col, col_type in [
        ("channels", "last_state_id", "TEXT"),
        ("messages", "agent_role", "TEXT"),
        ("messages", "thinking_content", "TEXT"),
        ("messages", "tools_used", "TEXT"),
        ("messages", "timeline", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def create_channel(name):
    channel_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    conn = _get_conn()
    conn.execute(
        "INSERT INTO channels (id, name, conversation_id) VALUES (?, ?, ?)",
        (channel_id, name, conversation_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    conn.close()
    return dict(row)


def get_channels():
    conn = _get_conn()
    rows = conn.execute("""
        SELECT c.*,
               m.content AS last_message,
               m.timestamp AS last_message_at
        FROM channels c
        LEFT JOIN (
            SELECT channel_id, content, timestamp,
                   ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY id DESC) AS rn
            FROM messages
            WHERE event_type IS NULL OR event_type IN ('message_created', 'assistant_message')
        ) m ON m.channel_id = c.id AND m.rn = 1
        ORDER BY c.created_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channel(channel_id):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        conn.close()
        return None
    channel = dict(row)
    messages = conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? ORDER BY id ASC",
        (channel_id,),
    ).fetchall()
    conn.close()
    channel["messages"] = [dict(m) for m in messages]
    return channel


def get_channel_by_conversation_id(conversation_id):
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM channels WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_channel_state_id(channel_id, state_id):
    conn = _get_conn()
    conn.execute(
        "UPDATE channels SET last_state_id = ? WHERE id = ?",
        (state_id, channel_id),
    )
    conn.commit()
    conn.close()


def add_message(
    channel_id,
    role,
    content="",
    event_type=None,
    image_base64=None,
    event_id=None,
    agent_role=None,
    thinking_content=None,
    tools_used=None,
    timeline=None,
):
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (channel_id, role, content, event_type, image_base64, event_id,
                agent_role, thinking_content, tools_used, timeline)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                role,
                content,
                event_type,
                image_base64,
                event_id,
                agent_role,
                thinking_content,
                tools_used,
                timeline,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        conn.close()
        raise


def upsert_thinking(channel_id, event_id, content, agent_role=None):
    conn = _get_conn()
    conn.execute(
        """INSERT INTO messages (channel_id, role, content, event_type, event_id, agent_role)
           VALUES (?, 'assistant', ?, 'thinking', ?, ?)
           ON CONFLICT(event_id) DO UPDATE SET content = excluded.content""",
        (channel_id, content, event_id, agent_role),
    )
    conn.commit()
    conn.close()


def get_messages(channel_id):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? ORDER BY id ASC",
        (channel_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_channel(channel_id):
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()
    conn.close()
