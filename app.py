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
    prompt = f"""
You extract events from emails.

Return ONLY valid JSON.

If the email is not an event:
{{"is_event": false}}

If it IS an event:
{{
  "is_event": true,
  "title": "...",
  "summary": "...",
  "start_time": "ISO8601",
  "location": "..."
}}

Email subject:
{subject}

Email body:
{body[:3000]}
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    try:
        return json.loads(resp.choices[0].message.content)
    except:
        return None

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
        maxResults=50
    ).execute().get("messages", [])

    added = 0

    # ---------- DETERMINISTIC SCAN ----------
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

        sender = next((h["value"] for h in headers if h["name"] == "From"), "").lower()
        subject_lower = subject.lower()

        likely_event = (
            any(x in sender for x in [
                "eventbrite", "meetup", "tickets", "universe", "calendar", "events"
            ])
            or any(x in subject_lower for x in [
                "you're registered", "your ticket", "event reminder", "rsvp", "invitation"
            ])
        )

        if not likely_event:
            continue

        if not re.search(
            r"\b(\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}:\d{2})\b",
            text,
            re.I
        ):
            continue

        date_match = re.search(
            r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b|\b\d{1,2}/\d{1,2})",
            text,
            re.I
        )
        if not date_match:
            continue

        try:
            when = dateparser.parse(date_match.group(1), fuzzy=True)
        except:
            continue

        save_gmail_event(
            gmail_id=m["id"],
            title=subject,
            summary=None,
            description=body[:2000],
            start_time=when.isoformat(),
            location=None,
        )
        added += 1

    # ---------- AI FALLBACK ----------
    if added == 0:
        for m in messages[:10]:
            msg = gmail.users().messages().get(
                userId="me",
                id=m["id"],
                format="full"
            ).execute()

            headers = msg["payload"]["headers"]
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
            body = extract_text(msg["payload"])

            ai = ai_extract_event(subject, body)
            if not ai or not ai.get("is_event") or not ai.get("start_time"):
                continue

            save_gmail_event(
                gmail_id=m["id"],
                title=ai["title"],
                summary=ai["summary"],
                description=body[:2000],
                start_time=ai["start_time"],
                location=ai.get("location"),
            )
            added += 1

    return f"""
    <h3>Scan complete</h3>
    <p>Saved <b>{added}</b> event candidates.</p>
    <a href="/review">🧾 Review events</a><br><br>
    <a href="/">⬅ Back to Home</a>
    """


@app.route("/review")
def review():
    event = get_next_pending_event()
    if not event:
        return """
        <h3>No pending events 🎉</h3>
        <a href="/">⬅ Back to Home</a>
        """

    event_id, gmail_id, title, summary, description, start_time, location = event

    return f"""
    <h3>{title}</h3>
    <p><b>When:</b> {start_time}</p>
    <p>{summary or description[:600]}</p>

    <form action="/accept/{event_id}" method="post">
        <button type="submit">✅ Add to Calendar</button>
    </form>

    <form action="/reject/{event_id}" method="post">
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

    _, _, title, summary, description, start_time, _ = event

    start = dateparser.parse(start_time)
    end = start + datetime.timedelta(hours=1)

    calendar.events().insert(
        calendarId="primary",
        body={
            "summary": title,
            "description": summary or description,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }
    ).execute()

    mark_event_status(event_id, "accepted")

    return """
    <h3>✅ Event added</h3>
    <a href="/review">Review next</a><br>
    <a href="/">Home</a>
    """


@app.route("/reject/<int:event_id>", methods=["POST"])
def reject(event_id):
    mark_event_status(event_id, "rejected")
    return redirect(url_for("review"))

