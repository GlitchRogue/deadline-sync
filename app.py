import os
import datetime
from flask import Flask, redirect, request, url_for, session
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
REDIRECT_URI = "https://deadline-sync.onrender.com"

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
    auth_url, _ = flow.authorization_url(prompt="consent")
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
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

    # ✅ STORE CREDS IN SESSION
    session["creds"] = {
        "token": flow.credentials.token,
        "refresh_token": flow.credentials.refresh_token,
        "token_uri": flow.credentials.token_uri,
        "client_id": flow.credentials.client_id,
        "client_secret": flow.credentials.client_secret,
        "scopes": flow.credentials.scopes,
    }

    return redirect(url_for("home"))

@app.route("/sync", methods=["POST"])
def sync():
    if "creds" not in session:
        return "Not connected to Google yet."

    creds = session["creds"]

    service = build(
        "calendar",
        "v3",
        credentials=Flow.from_client_config(
            {"web": creds},
            scopes=SCOPES
        ).credentials
    )

    start = datetime.datetime.utcnow()
    end = start + datetime.timedelta(minutes=30)

    event = {
        "summary": "Test Event from Deadline Sync",
        "start": {"dateTime": start.isoformat() + "Z"},
        "end": {"dateTime": end.isoformat() + "Z"},
    }

    service.events().insert(calendarId="primary", body=event).execute()
    return "Event created. Check your Google Calendar."
