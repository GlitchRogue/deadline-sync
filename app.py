import os
import datetime
import base64
import re

from flask import Flask, redirect, request, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from dateutil import parser as dateparser

from db import (
    init_db,
    save_creds,
    load_creds,
    save_gmail_event,
    get_next_pending_event,
    get_event_by_id,      # ← ADD THIS
    mark_event_status,
)


app = Flask(__name__)
init_db()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]

REDIRECT_URI = "https://deadline-sync.onrender.com/oauth2callback"


# -------------------- HELPERS --------------------

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


# -------------------- ROUTES --------------------

@app.route("/")
def home():
    return """
    <h2>Deadline Sync</h2>

    <a href="/connect">Connect Google</a><br><br>

    <form action="/sync" method="post">
      <button type="submit">Scan Gmail for events</button>
    </form>

    <br>
    <a href="/review">Review pending events</a>
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


@app.route("/sync", methods=["POST"])
def sync():
    gmail, _ = get_services()
    if not gmail:
        return "Not connected to Google."

    messages = gmail.users().messages().list(
        userId="me",
        maxResults=20
    ).execute().get("messages", [])

    added = 0

    for m in messages:
        msg = gmail.users().messages().get(
            userId="me",
            id=m["id"],
            format="full"
        ).execute()

        headers = msg["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        body = extract_text(msg["payload"])
        text = subject + "\n" + body

        # very loose MVP filter
        if not re.search(r"(event|ticket|rsvp|due|deadline|meetup|concert)", text, re.I):
            continue

        date_match = re.search(
            r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b)",
            text,
            re.I
        )
        if not date_match:
            continue

        try:
            when = dateparser.parse(date_match.group(1), fuzzy=True)
        except:
            continue

        sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")

        save_gmail_event(
            gmail_id=m["id"],
            title=subject or "Gmail event",
            description=f"FROM: {sender}\n\n{body[:1800]}",
            start_time=when.isoformat(),
        )
        added += 1

    return f"Saved {added} event candidates. Go to /review."


@app.route("/review")
def review():
    event = get_next_pending_event()

    if not event:
        return """
        <h3>No pending events 🎉</h3>

        <a href="/">⬅ Back to Home</a><br><br>
        <a href="/sync">🔄 Scan Gmail again</a>
        """

    event_id, gmail_id, title, description, start_time = event

    return f"""
    <h3>{title}</h3>

    <p><b>When:</b> {start_time}</p>

    <p style="white-space: pre-wrap;">{description[:600]}</p>

    <form action="/accept/{event_id}" method="post" style="display:inline;">
        <button type="submit">✅ Add to Calendar</button>
    </form>

    <form action="/reject/{event_id}" method="post" style="display:inline;">
        <button type="submit">❌ Skip</button>
    </form>

    <hr>

    <p>
      Reviewing pending Gmail events (one at a time)
    </p>

    <a href="/">⬅ Back to Home</a>
    """


@app.route("/accept/<int:event_id>", methods=["POST"])
def accept(event_id):
    _, calendar = get_services()
    if not calendar:
        return "Not connected."

    event = get_event_by_id(event_id)
    if not event:
        return redirect(url_for("review"))

    _, _, title, description, start_time = event

    start = dateparser.parse(start_time)
    end = start + datetime.timedelta(hours=1)

    cal_event = {
        "summary": f"[Gmail] {title}",
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
    }

    calendar.events().insert(calendarId="primary", body=cal_event).execute()
    mark_event_status(event_id, "accepted")

    return redirect(url_for("review"))


@app.route("/reject/<int:event_id>", methods=["POST"])
def reject(event_id):
    mark_event_status(event_id, "rejected")
    return redirect(url_for("review"))

