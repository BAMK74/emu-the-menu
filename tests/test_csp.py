"""The Content-Security-Policy header, the health endpoint, and templates that keep the policy
strict: no inline scripts, style blocks or event-handler attributes."""

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from emu_the_menu import web
from emu_the_menu.web import CONTENT_SECURITY_POLICY, create_app

TEMPLATES = Path(web.__file__).parent / "templates"


@pytest.fixture
def client(db_url):
    app = create_app(db_url, today=lambda: date(2026, 11, 10), fetch_product=lambda code: {})
    return TestClient(app, follow_redirects=False)


def test_the_policy_allows_only_emus_own_code_and_styles():
    rules = dict(rule.split(" ", 1) for rule in CONTENT_SECURITY_POLICY.split("; "))
    assert rules["default-src"] == "'self'"
    assert rules["script-src"] == "'self'"
    assert rules["style-src"] == "'self'"
    assert rules["frame-ancestors"] == "'none'"
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY
    assert "unsafe-inline" not in rules["script-src"] + rules["style-src"]


@pytest.mark.parametrize(
    "path",
    ["/", "/menu", "/recipes", "/settings", "/eaters", "/occasions", "/ingredients", "/disclaimer"],
)
def test_every_page_sends_the_policy(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", ["/static/js/shared.js", "/healthz", "/no-such-page"])
def test_files_errors_and_the_health_check_send_it_too(client, path):
    assert client.get(path).headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_the_health_check_answers_without_the_database():
    # Port 1 on localhost refuses at once: a page would fail, the health check must not.
    app = create_app("postgresql://emu:emu@127.0.0.1:1/emu")
    response = TestClient(app).get("/healthz")
    assert (response.status_code, response.text) == (200, "ok")


def template_texts():
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(TEMPLATES.glob("*.html"))]


def test_no_template_runs_inline_script():
    for name, text in template_texts():
        for attrs in re.findall(r"<script\b([^>]*)>", text):
            assert 'src="/static/' in attrs or 'type="application/json"' in attrs, (name, attrs)


def test_no_template_has_a_style_block_or_an_event_handler_attribute():
    for name, text in template_texts():
        assert "<style" not in text, name
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", text), name


def test_every_script_and_stylesheet_a_template_loads_exists():
    static = TEMPLATES.parent
    for name, text in template_texts():
        for src in re.findall(r'(?:src|href)="(/static/[^"?#]+)"', text):
            if "{{" not in src:
                assert (static / src.lstrip("/")).is_file(), (name, src)
