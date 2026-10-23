# Flask Quickstart

Local demo copy, adapted from the Flask documentation quickstart
(pallets/flask, docs/quickstart.rst, commit 9b74a90dd3c47f792734823e8793ac36f38bc4dd).
Code blocks are written as shell commands so that every step can be executed as written.

## A Minimal Application

Install Flask:

```bash
pip install Flask
```

A minimal Flask application looks like this. Save it as `hello.py`:

```bash
cat > hello.py <<'EOF'
from flask import Flask

app = Flask(__name__)

@app.route("/")
def hello_world():
    return "<p>Hello, World!</p>"
EOF
```

To run the application, use the `flask` command and point it at your application:

```bash
flask --app hello run
```

This launches a very simple builtin server. Head over to http://127.0.0.1:5000/ in your browser and you should see the greeting "Hello, World!" on the page.

If another program is already using port 5000 you will see an "Address already in use" error when the server tries to start.

Now that you have a working application, continue with the Flask documentation to learn about routing, templates and request data.
