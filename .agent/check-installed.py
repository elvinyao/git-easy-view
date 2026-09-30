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
    for locale, headline in (
        ("zh-CN", "看清每条分支的进展"),
        ("ja", "各ブランチの進捗を把握"),
        ("en", "See the progress of every branch"),
    ):
        response = client.get("/login", params={"lang": locale})
        assert response.status_code == 200 and headline in response.text, locale
        assert response.headers["content-language"] == locale, locale
application.state.engine.dispose()
from app.web import templates
for name in ("git_workspace.html", "git_log.html", "git_icons.html", "project.html", "project_commit.html"):
    templates.env.get_template(name)
print("Installed wheel: three language catalogs, login template, DB readiness, CSS, JavaScript, jQuery and license all available")
