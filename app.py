import os
from flask import Flask, redirect, request, url_for
from google_auth_oauthlib.flow import Flow

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET"]

SCOPES = ["https://www.googleapis.com/auth/calendar"]
REDIRECT_URI = "https://deadline-sync.onrender.com"

@app.route("/")
def home():
    return """
    <h2>Deadline Sync</h2>
    <a href="/connect">Connect Google Calendar</a>
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
    return "OAuth callback reached. Login worked."

if __name__ == "__main__":
    app.run()
