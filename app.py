import os
import datetime
from flask import Flask, redirect, request, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from db import init_db, save_creds, load_creds

app = Flask(__name__)

init_db()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
REDIRECT_URI = "https://deadline-sync.onrender.com/oauth2callback"

@app.route("/")
def home():
    return """
    <h2>Deadline Sync</h2>
    <a href="/connect">Connect Google Calendar</a>
    <form action="/sync" method="post">
      <button type="submit">Create test event</button>
    </form>
    """

@app.route("/connect")
def connect():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    global GOOGLE_CREDS

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    flow.fetch_token(code=request.args["code"])
    save_creds(flow.credentials)

    return redirect(url_for("home"))

@app.route("/sync", methods=["POST"])
def sync():
    row = load_creds()
    if not row:
        return "Not connected to Google yet."

    creds = Credentials(
        token=row[0],
        refresh_token=row[1],
        token_uri=row[2],
        client_id=row[3],
        client_secret=row[4],
        scopes=row[5].split(),
    )

    service = build("calendar", "v3", credentials=creds)

    start = datetime.datetime.utcnow()
    end = start + datetime.timedelta(minutes=30)

    event = {
        "summary": "Test Event from Deadline Sync",
        "description": "If you see this, persistence WORKS.",
        "start": {"dateTime": start.isoformat() + "Z"},
        "end": {"dateTime": end.isoformat() + "Z"},
    }

    service.events().insert(calendarId="primary", body=event).execute()
    return "Event created. Check Google Calendar."

