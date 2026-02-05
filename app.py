import os
import json
import datetime
import base64
import re

from flask import Flask, redirect, request, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from dateutil import parser as dateparser
from dateutil import tz
from openai import OpenAI

from db import (
    init_db,
    save_creds,
    load_creds,
    save_gmail_event,
    get_next_pending_event,
    get_event_by_id,
    mark_event_status,
)

# -------------------- SETUP --------------------

app = Flask(__name__)
init_db()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]

REDIRECT_URI = "https://deadline-sync.onrender.com/oauth2callback"
USER_TZ = tz.gettz("America/New_York")

# -------------------- HEURISTICS --------------------

NEGATIVE_HINTS = [
    "unsubscribe", "newsletter", "sale", "promo", "promotion",
    "deal", "discount"
]

POSITIVE_HINTS = [
    "appointment", "scheduled", "reminder", "pickup",
    "delivery", "reservation", "booking", "deadline", "due"
]

SOCIAL_HINTS = [
    "free pizza", "pizza", "free food", "snacks",
    "meeting", "event", "hangout", "social",
    "club", "talk", "seminar"
]

# -------------------- HELPERS --------------------

def normalize_dt(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=USER_TZ)
    return dt


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


def looks_like_event(subject, sender, body):
    t = (subject + "\n" + body).lower()
    score = 0

    if any(x in t for x in NEGATIVE_HINTS):
        score -= 1

    if any(x in t for x in POSITIVE_HINTS):
        score += 2

    if any(x in t for x in SOCIAL_HINTS):
        score += 2

    if re.search(r"\b(\d{1,2}(:\d{2})?\s?(am|pm))\b", t):
        score += 1

    if re.search(
        r"\b(?:today|tomorrow|tonight|friday|saturday|sunday|monday|tuesday|wednesday|thursday)\b",
        t,
    ):
        score += 1

    if re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
        t,
    ):
        score += 1

    return score >= 1, score   # MVP MODE: permissive


def parse_datetime(text):
    try:
        dt = dateparser.parse(text, fuzzy=True)
        if dt:
            dt = normalize_dt(dt)
            if dt.hour == 0 and dt.minute == 0:
                dt = dt.replace(hour=9)
            return dt
    except:
        return None
    return None


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

    return (
        build("gmail", "v1", credentials=creds),
        build("calendar", "v3", credentials=creds),
    )

# -------------------- ROUTES --------------------

@app.route("/")
def home():
    return """
    <h2>Deadline Sync</h2>

    <p><a href="/connect">Connect Google</a></p>

    <form action="/sync" method="post">
        <button type="submit">Scan Gmail for events</button>
    </form>

    <p><a href="/review">Review pending events</a></p>
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
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
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
        return "Not connected."

    messages = gmail.users().messages().list(
        userId="me",
        maxResults=50
    ).execute().get("messages", [])

    added = 0
    now = datetime.datetime.now(tz=USER_TZ)

    for m in messages:
        msg = gmail.users().messages().get(
            userId="me", id=m["id"], format="full"
        ).execute()

        headers = msg["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "")
        body = extract_text(msg["payload"])

        ok, score = looks_like_event(subject, sender, body)
        if not ok:
            continue

        dt = parse_datetime(subject + "\n" + body)

        # HARD FALLBACK — never silently drop
        if not dt:
            dt = now + datetime.timedelta(days=1)
            dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)

        save_gmail_event(
            gmail_id=m["id"],
            title=subject[:180] or "Event",
            summary=None,
            description=body[:2000],
            start_time=dt.isoformat(),
            location=None,
        )
        added += 1

    return f"""
    <h3>Scan complete</h3>
    <p>Saved <b>{added}</b> event candidates.</p>
    <p><a href="/review">Review events</a></p>
    <p><a href="/">Back home</a></p>
    """


@app.route("/review")
def review():
    event = get_next_pending_event()
    if not event:
        return """
        <h3>No pending events 🎉</h3>
        <p><a href="/">Back home</a></p>
        """

    # event is a dict (from sqlite row_factory) OR tuple
    # support BOTH safely
    if isinstance(event, dict):
        event_id = event["id"]
        title = event.get("title")
        summary = event.get("summary")
        description = event.get("description")
        start_time = event.get("start_time")
    else:
        event_id = event[0]
        title = event[2]
        summary = event[3]
        description = event[4]
        start_time = event[5]

    return f"""
    <h3>{title}</h3>
    <p><b>When:</b> {start_time}</p>
    <p>{summary or description[:600]}</p>

    <form action="/accept/{event_id}" method="post">
        <button type="submit">Add to Calendar</button>
    </form>

    <form action="/reject/{event_id}" method="post">
        <button type="submit">Skip</button>
    </form>

    <p><a href="/">Back home</a></p>
    """

@app.route("/accept/<int:event_id>", methods=["POST"])
def accept(event_id):
    _, calendar = get_services()
    event = get_event_by_id(event_id)

    if not calendar or not event:
        return redirect(url_for("review"))

    # Support tuple OR dict
    if isinstance(event, dict):
        title = event.get("title") or "Event"
        summary = event.get("summary")
        description = event.get("description") or ""
        location = event.get("location")
        start_time = event.get("start_time")
    else:
        title = event[2] or "Event"
        summary = event[3]
        description = event[4] or ""
        start_time = event[5]
        location = event[7] if len(event) > 7 else None

    start = normalize_dt(dateparser.parse(start_time))
    if not start:
        mark_event_status(event_id, "rejected")
        return redirect(url_for("review"))

    end = start + datetime.timedelta(hours=1)

    calendar.events().insert(
        calendarId="primary",
        body={
            "summary": title,
            "location": location,
            "description": summary or description,
            "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
            "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"},
        }
    ).execute()

    mark_event_status(event_id, "accepted")
    return redirect(url_for("review"))


@app.route("/reject/<int:event_id>", methods=["POST"])
def reject(event_id):
    mark_event_status(event_id, "rejected")
    return redirect(url_for("review"))
