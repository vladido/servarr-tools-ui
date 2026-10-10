import html
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from .client import EngineClient, EngineError, SonarrClient


_USEFUL_ACTIONS = {
    "rollarr": {"series_added", "grab_submitted", "update_submitted", "import_confirmed", "pending_recovered"},
    "normalizer": {"renamed"},
    "auto-add": {"added", "repaired"},
}
_AUDIT_FETCH_LIMIT = 200
_STATIC_DIR = Path(__file__).parent / "static"
_CSS_VERSION = hashlib.sha256((_STATIC_DIR / "app.css").read_bytes()).hexdigest()[:12]
_JS_VERSION = hashlib.sha256((_STATIC_DIR / "app.js").read_bytes()).hexdigest()[:12]


class Application:
    def __init__(self, config):
        self.config = config
        self.client = EngineClient(config.token, config.timeout, config.max_payload)
        self.sonarr = (
            SonarrClient(
                config.sonarr_url, config.sonarr_api_key, config.timeout, config.max_payload
            )
            if config.sonarr_url and config.sonarr_api_key
            else None
        )

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        if environ.get("HTTP_HOST") not in self.config.allowed_hosts:
            return self._json_response(start_response, 421, {"error": "host not allowed"})
        if method == "GET" and path in ("/static/app.css", "/static/app.js"):
            filename = path.rsplit("/", 1)[-1]
            body = (_STATIC_DIR / filename).read_bytes()
            content_type = "text/css; charset=utf-8" if filename.endswith(".css") else "text/javascript; charset=utf-8"
            return self._response(
                start_response,
                200,
                body,
                content_type,
                [("Cache-Control", "public, max-age=3600")],
            )
        if method == "GET" and path == "/healthz":
            return self._json_response(
                start_response,
                200,
                {"status": "ok", "services_configured": len(self.config.services)},
            )
        if method == "GET" and path.startswith("/api/posters/"):
            raw_id = path[len("/api/posters/") :]
            if not re.fullmatch(r"[1-9][0-9]*", raw_id):
                return self._json_response(start_response, 400, {"error": "invalid series id"})
            try:
                series_id = int(raw_id)
            except ValueError:
                return self._json_response(start_response, 400, {"error": "invalid series id"})
            if self.sonarr is None:
                return self._json_response(start_response, 404, {"error": "poster unavailable"})
            try:
                content_type, body = self.sonarr.poster(series_id)
            except EngineError:
                return self._json_response(start_response, 502, {"error": "poster unavailable"})
            return self._response(
                start_response, 200, body, content_type, [("Cache-Control", "no-store")]
            )
        if method == "POST" and path.startswith("/api/services/") and path.endswith("/runs"):
            if environ.get("HTTP_X_SERVARR_UI") != "1":
                return self._json_response(
                    start_response, 403, {"error": "same-origin request required"}
                )
            slug = path[len("/api/services/") : -len("/runs")].strip("/")
            service = next((item for item in self.config.services if item.slug == slug), None)
            if service is None:
                return self._json_response(start_response, 404, {"error": "unknown service"})
            try:
                status = self.client.status(service.url)
                if not status.get("capabilities", {}).get("run_now"):
                    return self._json_response(start_response, 409, {"error": "run now disabled"})
                result = self.client.run_now(service.url, str(uuid.uuid4()))
            except EngineError as exc:
                return self._json_response(start_response, 502, {"error": str(exc)})
            return self._json_response(start_response, 202, result)
        if method == "GET" and path == "/api/audit":
            query = parse_qs(environ.get("QUERY_STRING", ""))
            try:
                limit = min(100, max(1, int(query.get("limit", ["25"])[0])))
            except ValueError:
                return self._json_response(start_response, 400, {"error": "invalid limit"})
            items = []
            errors = {}
            useful_actions = {}
            for service in self.config.services:
                try:
                    service_rows = self.client.audit(service.url, _AUDIT_FETCH_LIMIT)
                    useful_actions[service.slug] = self._useful_actions(
                        service.slug, service_rows
                    )
                    for row in service_rows:
                        items.append({"service": service.slug, **row})
                except EngineError as exc:
                    errors[service.slug] = str(exc)
                    useful_actions[service.slug] = []
            items.sort(key=lambda row: row["timestamp"], reverse=True)
            return self._json_response(
                start_response,
                200,
                {
                    "items": items[:limit],
                    "errors": errors,
                    "useful_actions": useful_actions,
                },
            )
        if method == "GET" and path == "/api/status":
            services = {}
            for service in self.config.services:
                try:
                    services[service.slug] = self.client.status(service.url)
                except EngineError as exc:
                    services[service.slug] = {
                        "health": "unavailable",
                        "error": str(exc),
                        "capabilities": {"run_now": False},
                    }
            body = json.dumps(
                {"services": services}, separators=(",", ":"), allow_nan=False
            ).encode()
            return self._response(
                start_response,
                200,
                body,
                "application/json; charset=utf-8",
                [("Cache-Control", "no-store")],
            )
        if method == "GET" and path == "/":
            cards = []
            audit_items = []
            audit_errors = {}
            for service in self.config.services:
                try:
                    status = self.client.status(service.url)
                    error = None
                except EngineError as exc:
                    status = {"health": "unavailable", "capabilities": {"run_now": False}}
                    error = str(exc)
                useful_actions = []
                try:
                    service_rows = self.client.audit(service.url, _AUDIT_FETCH_LIMIT)
                    useful_actions = self._useful_actions(service.slug, service_rows)
                    audit_items.extend({"service": service.slug, **row} for row in service_rows)
                except EngineError as exc:
                    audit_errors[service.slug] = (service.name, str(exc))
                cards.append(self._card(service, status, error, useful_actions))
            audit_items.sort(key=lambda row: row["timestamp"], reverse=True)
            rows = "".join(self._audit_row(row) for row in audit_items[:25])
            errors = "".join(
                self._audit_error(slug, name, error)
                for slug, (name, error) in audit_errors.items()
            )
            body = self._page("".join(cards), rows, errors).encode()
            return self._response(start_response, 200, body, "text/html; charset=utf-8")
        return self._response(start_response, 404, b"not found", "text/plain; charset=utf-8")

    @staticmethod
    def _json_response(start_response, code, value):
        body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        return Application._response(
            start_response,
            code,
            body,
            "application/json; charset=utf-8",
            [("Cache-Control", "no-store")],
        )

    @staticmethod
    def _response(start_response, code, body, content_type, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Content-Security-Policy", "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
        ]
        headers.extend(extra_headers or [])
        start_response(f"{code} {HTTPStatus(code).phrase}", headers)
        return [body]

    @staticmethod
    def _time(timestamp):
        if not timestamp:
            return "—"
        escaped = html.escape(str(timestamp), quote=True)
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            fallback = parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            fallback = str(timestamp)
        return f'<time datetime="{escaped}" data-timestamp="{escaped}">{html.escape(fallback)}</time>'

    @staticmethod
    def _rounded_duration(value):
        if value is None:
            return None
        return f"{value:.1f}".rstrip("0").rstrip(".") + " s"

    @classmethod
    def _last_run(cls, value):
        if not isinstance(value, dict):
            return "—"
        parts = []
        timestamp = value.get("finished_at") or value.get("started_at")
        if timestamp:
            parts.append(cls._time(timestamp))
        for field in ("trigger", "detail"):
            if value.get(field):
                parts.append(html.escape(str(value[field]), quote=True))
        duration = cls._rounded_duration(value.get("duration_seconds"))
        if duration:
            escaped_value = html.escape(str(value["duration_seconds"]), quote=True)
            parts.append(
                f'<span data-duration-seconds="{escaped_value}">{duration}</span>'
            )
        return " · ".join(parts) or "—"

    @classmethod
    def _actions(cls, values):
        if not values:
            return "—"
        items = []
        for value in values:
            parts = [cls._time(value.get("timestamp"))]
            parts.extend(
                html.escape(str(value[field]), quote=True)
                for field in ("status", "reason", "subject")
                if value.get(field)
            )
            detail = f'<span class="action-detail">{" · ".join(parts)}</span>'
            series_id = value.get("series_id")
            poster = ""
            if type(series_id) is int and series_id > 0:
                poster = (
                    '<span class="action-poster">'
                    f'<img src="/api/posters/{series_id}" width="40" height="60" loading="lazy" alt="">'
                    "</span>"
                )
            items.append(f"<li>{poster}{detail}</li>")
        return f'<ul class="useful-actions">{"".join(items)}</ul>'

    @staticmethod
    def _useful_actions(slug, rows):
        allowed = _USEFUL_ACTIONS.get(slug, set())
        matches = [row for row in rows if row.get("status") in allowed]
        return sorted(
            matches, key=lambda row: row.get("timestamp", ""), reverse=True
        )[:5]

    @classmethod
    def _card(cls, service, status, error, useful_actions=None):
        esc = lambda value: html.escape(str(value), quote=True)
        summary = ", ".join(f"{esc(k)}: {esc(v)}" for k, v in status.get("summary", {}).items()) or "—"
        run_disabled = "" if status.get("capabilities", {}).get("run_now") else " disabled"
        mode = "dry-run" if status.get("dry_run") else "live"
        if "dry_run" not in status:
            mode = "unknown"
        detail_error = status.get("last_error") or error or "—"
        health = status.get("health", "unavailable")
        health_class = health if health in {"healthy", "degraded", "unavailable"} else "unavailable"
        return f'''<article class="card" data-service="{esc(service.slug)}">
<h2>{esc(service.name)}</h2><p class="health health-{health_class}" data-field="health" data-health="{esc(health_class)}">{esc(health)}</p>
<dl><dt data-i18n="mode">Mode</dt><dd data-field="mode">{mode}</dd><dt data-i18n="running">Running</dt><dd data-field="running">{esc(status.get("running", False))}</dd>
<dt data-i18n="queued">Queued</dt><dd data-field="queued">{esc(status.get("queued", 0))}</dd><dt data-i18n="last_run">Last run</dt><dd data-field="last_run">{cls._last_run(status.get("last_run"))}</dd>
<dt data-i18n="last_success">Last successful cycle</dt><dd data-field="last_success_at">{cls._time(status.get("last_success_at"))}</dd>
<dt data-i18n="useful_actions">Recent useful actions</dt><dd data-field="useful_actions">{cls._actions(useful_actions)}</dd>
<dt data-i18n="last_error">Last error</dt><dd data-field="last_error">{esc(detail_error)}</dd><dt data-i18n="summary">Summary</dt><dd data-field="summary">{summary}</dd></dl>
<button type="button" data-run-now="{esc(service.slug)}" data-i18n="run_now"{run_disabled}>Run now</button>
<p class="run-feedback" role="status" aria-live="polite" data-run-feedback></p></article>'''

    @staticmethod
    def _audit_row(row):
        cells = Application._time(row.get("timestamp"))
        cells = f"<td>{cells}</td>" + "".join(
            f"<td>{html.escape(str(row.get(field, '')), quote=True)}</td>"
            for field in ("service", "status", "reason", "subject")
        )
        return f"<tr>{cells}</tr>"

    @staticmethod
    def _audit_error(slug, name, error):
        esc = lambda value: html.escape(str(value), quote=True)
        return f'<li data-audit-error="{esc(slug)}">{esc(name)}: {esc(error)}</li>'

    @staticmethod
    def _page(cards, audit_rows, audit_errors):
        return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Servarr Tools</title><link rel="stylesheet" href="/static/app.css?v={_CSS_VERSION}"></head><body><header><div><h1>Servarr Tools</h1><p data-i18n="subtitle">Unified operations dashboard</p></div><label class="language-picker"><span data-i18n="language">Language</span><select id="language-select" aria-label="Language"><option value="en">English</option><option value="ru">Русский</option></select></label></header><main><section id="services" class="cards">{cards}</section><section><h2 data-i18n="recent_audit">Recent audit</h2><ul id="audit-errors" class="audit-errors" aria-live="polite">{audit_errors}</ul><table><thead><tr><th data-i18n="time">Time</th><th data-i18n="service">Service</th><th data-i18n="status">Status</th><th data-i18n="reason">Reason</th><th data-i18n="subject">Subject</th></tr></thead><tbody id="audit-body">{audit_rows}</tbody></table></section></main><script src="/static/app.js?v={_JS_VERSION}" defer></script></body></html>'''
