from flask import Flask

app = Flask(__name__)

GOOGLE_CREDS = None

@app.route("/")
def home():
    return """
    <h2>State Test</h2>
    <a href="/set">Set creds</a>
    <form action="/check" method="post">
      <button type="submit">Check creds</button>
    </form>
    """

@app.route("/set")
def set_creds():
    global GOOGLE_CREDS
    GOOGLE_CREDS = "SET"
    return "CREDS SET"

@app.route("/check", methods=["POST"])
def check():
    return f"CREDS = {GOOGLE_CREDS}"
