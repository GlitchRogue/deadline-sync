import sqlite3
import datetime

DB_PATH = "app.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS google_creds (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token TEXT,
            refresh_token TEXT,
            token_uri TEXT,
            client_id TEXT,
            client_secret TEXT,
            scopes TEXT
        )
    """)

    # NEW unified table for ALL sources (gmail now, eventbrite later, etc.)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,                     -- "gmail", "eventbrite", etc.
            source_item_id TEXT NOT NULL,             -- gmail message id, event id, etc.
            title TEXT,
            summary TEXT,
            description TEXT,
            start_time TEXT,
            end_time TEXT,
            all_day INTEGER DEFAULT 0,
            location TEXT,
            status TEXT DEFAULT 'pending',            -- pending/accepted/rejected
            confidence REAL DEFAULT 0.5,
            raw TEXT,                                 -- optional: store raw snippet/json later
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # prevent duplicates per source
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_event_candidates_source_item
        ON event_candidates(source, source_item_id)
    """)

    conn.commit()
    conn.close()

def save_creds(creds):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO google_creds
        (id, token, refresh_token, token_uri, client_id, client_secret, scopes)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            token=excluded.token,
            refresh_token=excluded.refresh_token,
            token_uri=excluded.token_uri,
            client_id=excluded.client_id,
            client_secret=excluded.client_secret,
            scopes=excluded.scopes
    """, (
        creds.token,
        creds.refresh_token,
        creds.token_uri,
        creds.client_id,
        creds.client_secret,
        " ".join(creds.scopes),
    ))
    conn.commit()
    conn.close()

def load_creds():
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT token, refresh_token, token_uri, client_id, client_secret, scopes
        FROM google_creds WHERE id=1
    """).fetchone()
    conn.close()
    return tuple(row) if row else None

# Keep your old name for minimal changes
def save_gmail_event(gmail_id, title, summary, description, start_time, location=None, end_time=None, all_day=0, confidence=0.7, raw=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO event_candidates
        (source, source_item_id, title, summary, description, start_time, end_time, all_day, location, confidence, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "gmail", gmail_id, title, summary, description, start_time,
        end_time, int(all_day), location, float(confidence), raw
    ))
    conn.commit()
    conn.close()

def get_next_pending_event():
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT id, source, source_item_id, title, summary, description,
               start_time, end_time, all_day, location, confidence
        FROM event_candidates
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None

def get_event_by_id(event_id):
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT id, source, source_item_id, title, summary, description,
               start_time, end_time, all_day, location, confidence, status
        FROM event_candidates
        WHERE id=?
    """, (event_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def mark_event_status(event_id, status):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE event_candidates SET status=? WHERE id=?
    """, (status, event_id))
    conn.commit()
    conn.close()

def gmail_event_exists(gmail_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1
        FROM event_candidates
        WHERE source = 'gmail'
          AND source_item_id = ?
        LIMIT 1
    """, (gmail_id,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

