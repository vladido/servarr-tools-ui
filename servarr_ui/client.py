import json
import math
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())

_STATUS_STRING_LIMITS = {"last_run": 256, "last_error": 1000}
_SUMMARY_KEY_LIMIT = 64
_SUMMARY_STRING_LIMIT = 1000
_AUDIT_STRING_LIMITS = {
    "timestamp": 256,
    "status": 64,
    "reason": 1000,
    "subject": 1000,
}
_URL_MARKER = re.compile(
    r"(?:\b(?:https?|ftps?|file|data|javascript|mailto):|(?:^|\s)//)",
    re.IGNORECASE,
)
_POSTER_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_POSTER_PAYLOAD = 2 * 1024 * 1024


def urlopen(request, timeout):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class EngineError(Exception):
    pass


class PayloadError(EngineError):
    pass


def _reject_invalid_unicode(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise PayloadError("upstream returned invalid Unicode") from exc
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_invalid_unicode(key)
            _reject_invalid_unicode(item)
    elif isinstance(value, list):
        for item in value:
            _reject_invalid_unicode(item)


def _reject_nonstandard_constant(_value):
    raise PayloadError("upstream returned invalid JSON")


def _bounded_scalar_summary(value):
    if not isinstance(value, dict):
        raise PayloadError("invalid status summary")
    result = {}
    for key, item in list(value.items())[:20]:
        if not isinstance(key, str):
            raise PayloadError("invalid status summary key")
        if isinstance(item, float) and not math.isfinite(item):
            raise PayloadError("invalid status summary value")
        if type(item) not in (str, int, float, bool):
            continue
        result[key[:_SUMMARY_KEY_LIMIT]] = (
            item[:_SUMMARY_STRING_LIMIT] if isinstance(item, str) else item
        )
    return result


def _canonical_timestamp(value, error_message):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PayloadError(error_message)
    if isinstance(value, float) and not math.isfinite(value):
        raise PayloadError(error_message)
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise PayloadError(error_message) from exc
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _normalize_last_run(value):
    if value is None:
        return None, {}, None
    if isinstance(value, str):
        try:
            timestamp = _canonical_timestamp(value, "invalid status field: last_run")
        except PayloadError:
            return {"detail": value[: _STATUS_STRING_LIMITS["last_run"]]}, {}, None
        return {"finished_at": timestamp}, {}, None
    if not isinstance(value, dict):
        raise PayloadError("invalid status field: last_run")

    result = {}
    for field in ("started_at", "finished_at"):
        if value.get(field) is not None:
            result[field] = _canonical_timestamp(
                value[field], f"invalid last run field: {field}"
            )
    for field in ("trigger", "source"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str):
                raise PayloadError(f"invalid last run field: {field}")
            result["trigger"] = item[:64]
            break
    duration = value.get("duration_seconds")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise PayloadError("invalid last run field: duration_seconds")
        if isinstance(duration, float) and not math.isfinite(duration):
            raise PayloadError("invalid last run field: duration_seconds")
        result["duration_seconds"] = duration

    summary_value = value.get("summary")
    summary = _bounded_scalar_summary({} if summary_value is None else summary_value)
    latest_error = None
    for field in ("error", "error_class"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str):
                raise PayloadError(f"invalid last run field: {field}")
            if item:
                latest_error = item[: _STATUS_STRING_LIMITS["last_error"]]
                break
    if not result:
        result["detail"] = "failed" if latest_error else "completed"
    return result, summary, latest_error


def _audit_text(item, field, fallback=None):
    value = item.get(field)
    if value is None and fallback is not None:
        value = item.get(fallback)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PayloadError(f"invalid audit field: {field}")
    return value


def _audit_timestamp(item):
    value = item.get("timestamp")
    if value is None:
        value = item.get("at")
    if value is None:
        return ""
    return _canonical_timestamp(value, "invalid audit field: timestamp")


def _audit_subject(item):
    legacy = item.get("subject")
    if legacy is not None:
        if not isinstance(legacy, str):
            raise PayloadError("invalid audit field: subject")
        values = [legacy]
    else:
        values = []
        for field in ("old_name", "new_name", "candidate_title"):
            value = item.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise PayloadError(f"invalid audit field: {field}")
                values.append(value)
        series_id = item.get("series_id")
        if type(series_id) is int and series_id > 0:
            values.append(f"series #{series_id}")
    safe = [value for value in values if not _URL_MARKER.search(value)]
    return " → ".join(safe)[: _AUDIT_STRING_LIMITS["subject"]]


class EngineClient:
    def __init__(self, token: str, timeout: float, max_payload: int):
        self.token = token
        self.timeout = timeout
        self.max_payload = max_payload

    def _json(self, url: str, method: str = "GET", body: bytes | None = None, extra_headers=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        headers.update(extra_headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        declared_length = int(length)
                    except ValueError as exc:
                        raise PayloadError("upstream returned invalid Content-Length") from exc
                    if declared_length < 0 or declared_length > self.max_payload:
                        raise PayloadError("upstream payload too large")
                raw = response.read(self.max_payload + 1)
        except HTTPError as exc:
            raise EngineError(f"upstream returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EngineError("upstream unavailable") from exc
        if len(raw) > self.max_payload:
            raise PayloadError("upstream payload too large")
        try:
            value = json.loads(raw, parse_constant=_reject_nonstandard_constant)
        except RecursionError as exc:
            raise PayloadError("upstream JSON is too deeply nested") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PayloadError("upstream returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PayloadError("upstream JSON must be an object")
        try:
            _reject_invalid_unicode(value)
        except RecursionError as exc:
            raise PayloadError("upstream JSON is too deeply nested") from exc
        return value

    def status(self, base_url: str) -> dict:
        raw = self._json(base_url.rstrip("/") + "/v1/status")
        contract_fields = {
            "service", "version", "health", "dry_run", "running", "queued",
            "last_run", "last_success_at", "last_error", "capabilities", "run_now",
        }
        if not contract_fields.intersection(raw):
            raise PayloadError("upstream JSON is not a status object")
        result = {}
        for field in ("dry_run", "running", "queued"):
            if field in raw:
                if not isinstance(raw[field], bool):
                    raise PayloadError(f"invalid status field: {field}")
                result[field] = raw[field]
        for field in ("service", "version"):
            if field in raw and not isinstance(raw[field], str):
                raise PayloadError(f"invalid status field: {field}")
        explicit_health = raw.get("health")
        if explicit_health is not None:
            if explicit_health not in ("healthy", "degraded", "unavailable"):
                raise PayloadError("invalid status field: health")

        capabilities = raw.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise PayloadError("invalid status field: capabilities")
        run_now = capabilities.get("run_now", capabilities.get("run_now_enabled", False))
        if "run_now" in raw:
            run_now = raw["run_now"]
        if not isinstance(run_now, bool):
            raise PayloadError("invalid status capability: run_now")
        result["capabilities"] = {"run_now": run_now}

        last_run, nested_summary, latest_error = _normalize_last_run(raw.get("last_run"))
        if last_run is not None:
            result["last_run"] = last_run
        result["summary"] = nested_summary or _bounded_scalar_summary(raw.get("summary", {}))

        last_error = raw.get("last_error")
        if last_error is not None and not isinstance(last_error, str):
            raise PayloadError("invalid status field: last_error")
        if not last_error:
            last_error = latest_error
        if last_error:
            result["last_error"] = last_error[: _STATUS_STRING_LIMITS["last_error"]]
        explicit_success = raw.get("last_success_at")
        if explicit_success is not None:
            result["last_success_at"] = _canonical_timestamp(
                explicit_success, "invalid status field: last_success_at"
            )
        elif not last_error and isinstance(last_run, dict) and last_run.get("finished_at"):
            result["last_success_at"] = last_run["finished_at"]
        result["health"] = "degraded" if last_error else (explicit_health or "healthy")
        return result

    def run_now(self, base_url: str, idempotency_key: str) -> dict:
        raw = self._json(
            base_url.rstrip("/") + "/v1/runs",
            method="POST",
            body=b"{}",
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        result = {}
        for field in ("run_id", "status"):
            value = raw.get(field)
            if value is not None and not isinstance(value, str):
                raise PayloadError(f"invalid run field: {field}")
            if value is not None:
                result[field] = value[:256]
        if "queued" in raw:
            queued = raw["queued"]
            if not isinstance(queued, bool):
                raise PayloadError("invalid run field: queued")
            if queued and "status" not in result:
                result["status"] = "queued"
        if "status" not in result:
            raise PayloadError("missing run field: status")
        return result

    def audit(self, base_url: str, limit: int) -> list[dict]:
        raw = self._json(base_url.rstrip("/") + f"/v1/audit?limit={limit}")
        items = raw.get("records", raw.get("items"))
        if not isinstance(items, list):
            raise PayloadError("audit records must be an array")
        result = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                raise PayloadError("audit item must be an object")
            try:
                timestamp = _audit_timestamp(item)
            except PayloadError:
                continue
            normalized: dict = {
                "timestamp": timestamp,
                "status": _audit_text(item, "status", "event")[: _AUDIT_STRING_LIMITS["status"]],
                "reason": _audit_text(item, "reason")[: _AUDIT_STRING_LIMITS["reason"]],
                "subject": _audit_subject(item),
            }
            series_id = item.get("series_id")
            if type(series_id) is int and series_id > 0:
                normalized["series_id"] = series_id
            result.append(normalized)
        return result


class SonarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, max_metadata: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.metadata_client = EngineClient("", timeout, max_metadata)

    @property
    def _headers(self):
        return {"X-Api-Key": self.api_key}

    def poster(self, series_id: int) -> tuple[str, bytes]:
        metadata = self.metadata_client._json(
            self.base_url + f"/api/v3/series/{series_id}",
            extra_headers=self._headers,
        )
        images = metadata.get("images")
        if not isinstance(images, list):
            raise PayloadError("invalid Sonarr series metadata")
        poster_path = None
        for image in images[:100]:
            if not isinstance(image, dict):
                raise PayloadError("invalid Sonarr series metadata")
            if image.get("coverType") == "poster":
                poster_path = image.get("url")
                break
        if not isinstance(poster_path, str):
            raise PayloadError("Sonarr poster unavailable")
        parsed = urlsplit(poster_path)
        decoded_path = unquote(parsed.path)
        relative_parts = decoded_path[len("/MediaCover/"):].split("/")
        if (
            parsed.scheme or parsed.netloc or parsed.fragment
            or not decoded_path.startswith("/MediaCover/")
            or "\\" in decoded_path
            or any(part in ("", ".", "..") for part in relative_parts)
        ):
            raise PayloadError("invalid Sonarr poster path")
        # Sonarr's browser-facing /MediaCover route redirects to its login page
        # when UI authentication is enabled. Fetch the same validated asset via
        # the authenticated API route instead; never forward the UI redirect or
        # expose the API key in a query string.
        poster_url = (
            self.base_url
            + "/api/v3/mediacover/"
            + parsed.path[len("/MediaCover/"):]
        )
        request = Request(
            poster_url,
            headers={"Accept": "image/jpeg,image/png,image/webp", **self._headers},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        declared_length = int(length)
                    except ValueError as exc:
                        raise PayloadError("invalid Sonarr poster length") from exc
                    if declared_length < 0 or declared_length > _MAX_POSTER_PAYLOAD:
                        raise PayloadError("Sonarr poster too large")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in _POSTER_CONTENT_TYPES:
                    raise PayloadError("invalid Sonarr poster content type")
                body = response.read(_MAX_POSTER_PAYLOAD + 1)
        except HTTPError as exc:
            raise EngineError(f"Sonarr returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EngineError("Sonarr unavailable") from exc
        if len(body) > _MAX_POSTER_PAYLOAD:
            raise PayloadError("Sonarr poster too large")
        return content_type, body
