from flask import Flask, render_template_string

app = Flask(__name__)

HTML = """
<!doctype html>
<title>Deadline Sync</title>
<h2>Deadline Sync MVP</h2>

<form action="/sync" method="post">
  <button type="submit">Sync now</button>
</form>

<p>{{ message }}</p>
"""

@app.route("/")
def home():
    return render_template_string(HTML, message="")

@app.route("/sync", methods=["POST"])
def sync():
    return render_template_string(HTML, message="Sync button works.")

if __name__ == "__main__":
    app.run()
