import sqlite3

DB_PATH = "app.db"

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Store Google OAuth credentials
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

    # Store extracted Gmail event candidates
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gmail_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT UNIQUE,
            title TEXT,
            description TEXT,
            start_time TEXT,
            status TEXT DEFAULT 'pending'
        )
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
    return row

def save_gmail_event(gmail_id, title, description, start_time):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO gmail_events
        (gmail_message_id, title, description, start_time)
        VALUES (?, ?, ?, ?)
    """, (gmail_id, title, description, start_time))
    conn.commit()
    conn.close()


def get_next_pending_event():
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT id, gmail_message_id, title, description, start_time
        FROM gmail_events
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 1
    """).fetchone()
    conn.close()
    return row


def mark_event_status(event_id, status):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE gmail_events
        SET status = ?
        WHERE id = ?
    """, (status, event_id))
    conn.commit()
    conn.close()

def get_event_by_id(event_id):
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT id, gmail_message_id, title, description, start_time
        FROM gmail_events
        WHERE id = ?
    """, (event_id,)).fetchone()
    conn.close()
    return row
