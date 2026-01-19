import os
import datetime
import base64
import re

from flask import Flask, redirect, request, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from db import init_db, save_creds, load_creds

app = Flask(__name__)
init_db()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]

REDIRECT_URI = "https://deadline-sync.onrender.com/oauth2callback"


@app.route("/")
def home():
    return """
    <h2>Deadline Sync</h2>
    <a href="/connect">Connect Google</a>
    <form action="/sync" method="post">
      <button type="submit">Sync Gmail → Calendar</button>
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
        prompt="consent",
    )
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
    save_creds(flow.credentials)
    return redirect(url_for("home"))


def get_services():
    row = load_creds()
    if not row:
        return None, None

    creds = Credentials(
        token=row[0],
        refresh_token=row[1],
        token_uri=row[2],
        client_id=row[3],
        client_secret=row[4],
        scopes=row[5].split(),
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_creds(creds)

    gmail = build("gmail", "v1", credentials=creds)
    calendar = build("calendar", "v3", credentials=creds)
    return gmail, calendar


def extract_text(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    data = payload["body"].get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""


@app.route("/sync", methods=["POST"])
def sync():
    gmail, calendar = get_services()
    if not gmail:
        return "Not connected."

    messages = gmail.users().messages().list(
        userId="me",
        maxResults=20
    ).execute().get("messages", [])

    created = 0

    for m in messages:
        msg = gmail.users().messages().get(
            userId="me",
            id=m["id"],
            format="full"
        ).execute()

        headers = msg["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        body = extract_text(msg["payload"])

        if not re.search(r"(due|deadline)", subject + body, re.I):
            continue

        date_match = re.search(r"\b(\w+ \d{1,2})\b", body)
        if not date_match:
            continue

        try:
            when = datetime.datetime.strptime(
                date_match.group(1) + " " + str(datetime.datetime.now().year),
                "%B %d %Y"
            )
        except:
            continue

        event = {
            "summary": subject[:100],
            "description": body[:2000],
            "start": {"dateTime": when.isoformat()},
            "end": {"dateTime": (when + datetime.timedelta(hours=1)).isoformat()},
        }

        calendar.events().insert(calendarId="primary", body=event).execute()
        created += 1

    return f"Created {created} events from Gmail."
