import sqlite3

DB_PATH = "app.db"

def get_conn():
    return sqlite3.connect(DB_PATH)

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
