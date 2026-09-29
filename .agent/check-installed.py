"""Run with python -I to verify the installed wheel independently of source."""
from pathlib import Path
from fastapi.testclient import TestClient
import app.main

assert "site-packages" in str(Path(app.main.__file__))
application = app.main.create_app()
with TestClient(application) as client:
    for path in ("/login", "/health", "/static/app.css", "/static/app.js",
                 "/static/vendor/jquery.min.js", "/static/vendor/jquery-LICENSE.txt"):
        assert client.get(path).status_code == 200, path
application.state.engine.dispose()
print("Installed wheel: login template, DB readiness, CSS, JavaScript, jQuery and license all available")
