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

# -------------------- APP SETUP --------------------

app = Flask(__name__)
init_db()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]

REDIRECT_URI = "https://deadline-sync.onrender.com/oauth2callback"

# -------------------- HELPERS --------------------
USER_TZ = tz.gettz("America/New_York")

NEGATIVE_HINTS = [
    "unsubscribe", "newsletter", "sale", "promo", "promotion", "marketing",
    "deal", "discount", "save", "offer", "new arrivals"
]

POSITIVE_HINTS = [
    "appointment", "scheduled", "reminder", "service", "pickup", "pick up",
    "ready for pickup", "order is ready", "reservation", "booking",
    "delivery", "arriving", "out for delivery", "delivery window",
    "bill due", "payment due", "due date", "deadline", "renewal",
    "inspection", "checkup", "installation", "estimate"
]

def normalize_dt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=USER_TZ)
    return dt

def parse_first_datetime(text):
    """
    Try to find a usable datetime.
    - If we find a date+time, use it.
    - If only date, default to 9:00 AM local to avoid dropping obvious events.
    """
    # Try dateparser fuzzy on a smaller chunk to avoid garbage matches
    try:
        dt = dateparser.parse(text, fuzzy=True)
        if dt:
            dt = normalize_dt(dt)
            return dt
    except:
        return None
    return None

def looks_like_event(subject, sender, body):
    t = (subject + "\n" + body).lower()

    # Hard block obvious marketing
    if any(x in t for x in NEGATIVE_HINTS):
        return False, 0

    score = 0
    if any(x in t for x in POSITIVE_HINTS):
        score += 2

    # Common “real world” signals
    if re.search(r"\b(appointment|scheduled|reminder|reservation|pickup|delivery|due)\b", t):
        score += 2

    # Time or date patterns increase score
    if re.search(r"\b(\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}:\d{2})\b", t, re.I):
        score += 1

    if re.search(r"\b(\d{1,2}/\d{1,2}(/\d{2,4})?)\b", t) or re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b",
        t, re.I
    ):
        score += 1

    # Sender/subject “ticket” sources still count
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    if any(x in sender_l for x in ["eventbrite", "meetup", "universe", "tickets"]):
        score += 2
    if any(x in subject_l for x in ["you're registered", "your ticket", "rsvp", "invitation"]):
        score += 2

    return score >= 3, score

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


def ai_extract_event(subject, body):
    # Keep it short and force constraints.
    prompt = f"""
You extract calendar-worthy events or deadlines from an email.

Return ONLY valid JSON. No markdown. No commentary.

Rules:
- If NOT an event/deadline/appointment/reminder: {{"is_event": false}}
- If it IS relevant: set is_event=true and fill fields.
- start_time MUST be RFC3339 / ISO8601 with timezone offset (e.g. 2026-02-03T09:00:00-05:00).
- If the email gives a DATE but NO TIME, choose 09:00 local time and set all_day=false.
- Summary should be 1 sentence, concrete, no fluff.
- Title should be short and specific (e.g. "Package pickup - UPS Store", "Oil change appointment").
- confidence is 0..1.

Output schema when is_event=true:
{{
  "is_event": true,
  "title": "...",
  "summary": "...",
  "start_time": "...",
  "end_time": null,
  "all_day": false,
  "location": null,
  "confidence": 0.0
}}

Email subject:
{subject}

Email body (truncated):
{body[:2500]}
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a strict information extractor."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    try:
        return json.loads(resp.choices[0].message.content)
    except:
        return None


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
        maxResults=50,
        q="newer_than:14d"
    ).execute().get("messages", [])

    added = 0
    now = datetime.datetime.now(tz=USER_TZ)

    for m in messages:
        msg = gmail.users().messages().get(
            userId="me",
            id=m["id"],
            format="full"
        ).execute()

        headers = msg["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "")
        body = extract_text(msg["payload"])

        ok, score = looks_like_event(subject, sender, body)
        if not ok:
            continue

        text = subject + "\n" + body

        # Try to parse a datetime deterministically
        dt = None

        # Prefer explicit date-like snippets when present
        date_snip = re.search(
            r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}(/\d{2,4})?\b)",
            text,
            re.I
        )
        if date_snip:
            try:
                dt = dateparser.parse(date_snip.group(1), fuzzy=True)
                dt = normalize_dt(dt)
                # If no time found anywhere, default to 9am
                if dt and not re.search(r"\b(\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}:\d{2})\b", text, re.I):
                    dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
            except:
                dt = None

        # If we still don't have dt, try fuzzy parse on subject first (often contains date)
        if not dt:
            dt = parse_first_datetime(subject)

        # Filter: only future-ish, avoid ancient junk
        if not dt:
            # We'll let AI handle it for strong candidates later
            continue

        if dt < now - datetime.timedelta(days=1):
            continue
        if dt > now + datetime.timedelta(days=180):
            continue

        save_gmail_event(
            gmail_id=m["id"],
            title=subject.strip()[:180] or "Event",
            summary=None,
            description=body[:2000],
            start_time=dt.isoformat(),
            location=None,
            confidence=min(0.9, 0.5 + (score * 0.1)),
            raw=None
        )
        added += 1

    # ---------- AI ENRICHMENT / FALLBACK ----------
    # Run AI on a small set of strong-looking emails where deterministic failed to add anything.
    # This catches package pickup, service reminders, reservations, etc.
    scanned_ai = 0
    for m in messages[:15]:
        if scanned_ai >= 8:
            break

        msg = gmail.users().messages().get(
            userId="me",
            id=m["id"],
            format="full"
        ).execute()

        headers = msg["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "")
        body = extract_text(msg["payload"])

        ok, score = looks_like_event(subject, sender, body)
        if not ok or score < 4:
            continue

        ai = ai_extract_event(subject, body)
        scanned_ai += 1

        if not ai or not ai.get("is_event") or not ai.get("start_time"):
            continue

        # Basic sanity: parse AI datetime
        try:
            dt = normalize_dt(dateparser.parse(ai["start_time"]))
        except:
            continue

        if not dt:
            continue

        if dt < now - datetime.timedelta(days=1) or dt > now + datetime.timedelta(days=180):
            continue

        save_gmail_event(
            gmail_id=m["id"],
            title=(ai.get("title") or subject).strip()[:180],
            summary=(ai.get("summary") or None),
            description=body[:2000],
            start_time=dt.isoformat(),
            end_time=ai.get("end_time"),
            all_day=1 if ai.get("all_day") else 0,
            location=ai.get("location"),
            confidence=float(ai.get("confidence") or 0.6),
            raw=json.dumps(ai)
        )
        added += 1
        
@app.route("/review")
def review():
    event = get_next_pending_event()
    if not event:
        return """
        <h3>No pending events 🎉</h3>
        <a href="/">⬅ Back to Home</a>
        """

    title = event.get("title") or "Untitled"
    when = event.get("start_time") or "Unknown"
    summary = event.get("summary")
    description = event.get("description") or ""
    location = event.get("location")

    body_text = summary or description[:600]
    loc_line = f"<p><b>Where:</b> {location}</p>" if location else ""

    return f"""
    <h3>{title}</h3>
    <p><b>When:</b> {when}</p>
    {loc_line}
    <p>{body_text}</p>

    <form action="/accept/{event['id']}" method="post">
        <button type="submit">✅ Add to Calendar</button>
    </form>

    <form action="/reject/{event['id']}" method="post">
        <button type="submit">❌ Skip</button>
    </form>

    <a href="/">⬅ Back to Home</a>
    """


@app.route("/accept/<int:event_id>", methods=["POST"])
def accept(event_id):
    _, calendar = get_services()
    event = get_event_by_id(event_id)
    if not calendar or not event:
        return redirect(url_for("review"))

    title = event.get("title") or "Event"
    summary = event.get("summary")
    description = event.get("description") or ""
    location = event.get("location")

    start = normalize_dt(dateparser.parse(event["start_time"]))
    if not start:
        mark_event_status(event_id, "rejected")
        return redirect(url_for("review"))

    # Default duration: 1 hour unless you later add end_time
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


