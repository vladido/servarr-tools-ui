# Servarr Tools UI

A standalone, read-mostly dashboard and backend-for-frontend (BFF) for Rollarr, Torrent Name Normalizer, and Sonarr Auto-add. It uses Python's standard library and static CSS/JavaScript; there is no frontend build step or runtime dependency installation.

[Полная русская версия / Full Russian README](README.ru.md)

## Installation

Container image: `ghcr.io/vladido/servarr-tools-ui:latest` ([GHCR](https://github.com/vladido/servarr-tools-ui/pkgs/container/servarr-tools-ui)).

Pin an available `sha-<commit>` tag or digest for a fixed version; preserve the previous digest before updating.

Create a separate installation directory and save the following YAML as `compose.yaml` (complete example from [compose.example.yml](compose.example.yml)):

```yaml
services:
  servarr-tools-ui:
    image: ghcr.io/vladido/servarr-tools-ui:latest
    restart: unless-stopped
    environment:
      ROLLARR_URL: http://rollarr-controller:8080
      NORMALIZER_URL: http://torrent-name-normalizer:8077
      AUTO_ADD_URL: http://sonarr-series-auto-add:8078
      SONARR_URL: http://sonarr:8989
      SONARR_API_KEY: ${SONARR_API_KEY:?set SONARR_API_KEY}
      SERVARR_TOOLS_TOKEN: ${SERVARR_TOOLS_TOKEN:?set SERVARR_TOOLS_TOKEN}
      SERVARR_TOOLS_ALLOWED_HOSTS: ${SERVARR_TOOLS_ALLOWED_HOSTS:-localhost:8787,127.0.0.1:8787}
      UPSTREAM_TIMEOUT_SECONDS: "2"
    ports:
      - "127.0.0.1:8787:8080"
    networks:
      - servarr
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true

networks:
  servarr:
    external: true
    name: ${SERVARR_NETWORK:-servarr}
```

Requires Docker with Compose, Sonarr, and the three engines with API v1 on the shared external `servarr` network. The network must exist; if needed, create it with `docker network create servarr` and attach the dependencies. Set another name with `SERVARR_NETWORK` in the Compose `.env`. The dashboard does not create or start engines.

Create `.env` in the installation directory with mode `0600`, replacing values with your own:

```dotenv
SONARR_API_KEY=replace-with-sonarr-api-key
SERVARR_TOOLS_TOKEN=replace-with-shared-random-ascii-token
```

Set the same `SERVARR_TOOLS_TOKEN` in all engines' protected env files and recreate them to apply it. This `.env` is used for Compose `${...}` interpolation, not as a container `env_file`. There is no built-in user login: the token protects engine requests, not browser access. Only `127.0.0.1:8787` is published; remote access requires a reverse proxy with external authentication, preserved `Host`, and exact `SERVARR_TOOLS_ALLOWED_HOSTS`. Do not publish engine ports.

```sh
chmod 600 .env
docker compose config --quiet
docker compose pull
docker compose up -d
```

Open `http://127.0.0.1:8787/`. The container runs as UID/GID `10001:10001`, keeps no persistent application state, mounts no Docker socket, and requires no writable state directory.

## Interface and operator workflow



1. The dashboard defaults to English. Choose **English** or **Русский** in **Language**; your selection is saved in the browser.
2. Check each service's mode, last error, and **last successful cycle**. Dashboard liveness and engine health are separate checks.
3. Review **Recent useful actions** and **Recent audit**. Routine `skipped` events are not failures; their reasons explain which check prevented action.
4. **Run now** queues a cycle for the selected engine. It does not switch dry-run/live, and live mode may perform engine-authorized qBittorrent/Sonarr changes. Do not click it merely to check availability.
5. If one engine is unavailable, inspect its API/configuration; other cards continue working. The dashboard does not manage containers, change settings, or implement its own importer.

## Behavior

- Server-renders service cards and recent audit rows, then polls the same-origin BFF to refresh them.
- Shows derived health, dry-run/live mode, running/queued booleans, a structured last run, the last successful cycle, up to five recent useful actions per service with optional Sonarr poster thumbnails, last error, scalar summary, and run-now capability.
- Normalizes ISO and Unix timestamps to RFC 3339 and lets the browser localize visible dates, times, and rounded durations with `Intl`.
- Treats every engine independently: a timeout, malformed response, or outage degrades only that card/audit source.
- Merges allowlisted audit fields (`service`, `timestamp`, `status`, `reason`, `subject`) and renders all upstream text as text, never unsafe HTML.
- Enables **Run now** only when the selected engine's current `/v1/status` capability allows it.
- Rejects requests whose exact HTTP `Host` value is not configured, before contacting any engine.

The browser only calls `/api/*` on this application. Engine and Sonarr URLs, the bearer token, and the Sonarr API key remain server-side. Poster images are fetched through `/api/posters/{series_id}` from fixed Sonarr configuration: the BFF reads the series metadata, accepts only its relative `/MediaCover/` poster path, maps that validated path to Sonarr's authenticated `/api/v3/mediacover/` route, rejects redirects, bounds both responses, and serves only JPEG, PNG, or WebP with `Cache-Control: no-store`. A failed or absent poster leaves a fixed-size neutral placeholder without affecting the action list. Run-now requires a same-origin-only custom request header (cross-site form posts are rejected), resolves a service slug against fixed configuration, rechecks capability, discards any browser request body and auth/idempotency headers, then sends exactly `{}` to the configured engine with the server token and a server-generated idempotency key.

## Engine contract

Each configured engine must expose:

- `GET /v1/status`
- `GET /v1/audit?limit=N`
- `POST /v1/runs`

The BFF sends the server-side `SERVARR_TOOLS_TOKEN` in an `Authorization` header with the Bearer scheme for all three endpoints. It accepts Rollarr's `capabilities.run_now` and the Normalizer/Auto-add top-level `run_now`, requires `queued` to be a JSON boolean, normalizes the deployed `last_run` object into structured fields, and lifts its scalar `summary`. Optional `last_success_at` is preserved independently of a failed latest run; when absent, a successful `last_run.finished_at` is used. Health is derived as `degraded` when the latest run error, `last_error`, or `error_class` is present and `healthy` otherwise; network and schema failures remain `unavailable`.

Audit responses use canonical `records` (legacy `items` remains accepted). The BFF fetches up to 200 rows independently from every service, normalizes `at`/`timestamp`, `event`/`status`, and `reason`, and derives up to five newest useful actions from service-specific event allowlists. Subjects are assembled only from bounded `subject`, `old_name`, `new_name`, `candidate_title`, and exact positive-integer `series_id` values; the latter is separately exposed only to form the same-origin poster route. URL-bearing values and all other upstream fields—including hashes and secrets—are excluded. Responses are bounded to 256 KiB and schema/fields are allowlisted. Upstream timeout is configurable only within 0.1–5 seconds and defaults to 2 seconds.

## Configuration

All values are fixed at process startup:

| Variable | Required | Meaning |
|---|---:|---|
| `ROLLARR_URL` | yes | Absolute HTTP(S) base URL |
| `NORMALIZER_URL` | yes | Absolute HTTP(S) base URL |
| `AUTO_ADD_URL` | yes | Absolute HTTP(S) base URL |
| `SONARR_URL` | yes | Absolute HTTP(S) Sonarr base URL used only by the BFF poster proxy |
| `SONARR_API_KEY` | yes | Sonarr API key used only server-side |
| `SERVARR_TOOLS_TOKEN` | yes | Shared server-side bearer token |
| `SERVARR_TOOLS_ALLOWED_HOSTS` | no | Comma-separated exact HTTP `Host` values; default `localhost:8787,127.0.0.1:8787` |
| `UPSTREAM_TIMEOUT_SECONDS` | no | Per-request timeout, default `2` |
| `LISTEN_HOST` | no | Bind address, default `0.0.0.0` |
| `LISTEN_PORT` | no | Listen port, default `8080` |

URLs containing embedded credentials and non-HTTP schemes are rejected. Allowed hosts must be valid hostnames, IPv4 addresses, or bracketed IPv6 addresses, each with an optional port. Wildcards, schemes, paths, and invalid ports are rejected at startup. Matching uses the HTTP `Host` header only; `X-Forwarded-Host` is not trusted. The app emits no CORS headers, so browser engine access is not enabled.

Reverse proxies must add the dashboard's public host (including its public port when the browser sends one) to `SERVARR_TOOLS_ALLOWED_HOSTS`. For example, use `SERVARR_TOOLS_ALLOWED_HOSTS=tools.example.com` for a default-port HTTPS deployment. Preserve the original `Host` header when proxying.

## BFF endpoints

- `GET /` — server-rendered dashboard
- `GET /api/status` — independently aggregated status map
- `GET /api/audit?limit=25` — merged, newest-first audit rows (`1..100`)
- `GET /api/posters/{positive-series-id}` — cacheless, fixed-target Sonarr poster proxy
- `POST /api/services/{rollarr|normalizer|auto-add}/runs` — capability-gated fixed-target run
- `GET /healthz` — local liveness/configuration count

All JSON API responses use `Cache-Control: no-store`. Security headers restrict script, style, and network access to the same origin.

## Access, troubleshooting, and updates

There is no built-in user login. The server token protects BFF-to-engine calls, not browser access. The Compose example binds only `127.0.0.1:8787`; when publishing through a reverse proxy, configure external authentication and allowlist the required `Host`. Do not expose engine APIs publicly.

```sh
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/api/status
```

- Rejected `Host`: match the browser's exact hostname/port to `SERVARR_TOOLS_ALLOWED_HOSTS`; preserve the original `Host` through the proxy.
- `unavailable`: check URL, internal networking, timeout, and engine image support for `/v1/status`/`/v1/audit`.
- `degraded`: the latest engine cycle failed; inspect the engine rather than only dashboard `/healthz`.
- Disabled button: inspect engine `run_now` and the shared token. Repeated/busy runs can return `409`.
- Missing poster: check Sonarr and its API key; actions work without images.

Update with `docker compose pull`, then `docker compose up -d`; verify `/healthz`, all three cards, and audit without manually triggering a live cycle. The dashboard keeps no persistent application state; recovery needs Compose, protected environment/secrets, and reverse-proxy settings. Pin a previous image tag/digest to roll back.

## Developers: optional source build

With Git and Docker, build a local development image and set `image` in a local Compose copy to `servarr-tools-ui:dev`. The configuration, secrets and network requirements above also apply to development.

```sh
git clone https://github.com/vladido/servarr-tools-ui.git
cd servarr-tools-ui
docker build -t servarr-tools-ui:dev .
```

### Run locally

```sh
export ROLLARR_URL=http://127.0.0.1:8101
export NORMALIZER_URL=http://127.0.0.1:8102
export AUTO_ADD_URL=http://127.0.0.1:8103
export SONARR_URL=http://127.0.0.1:8989
export SONARR_API_KEY='replace-me'
export SERVARR_TOOLS_TOKEN='replace-me'
export SERVARR_TOOLS_ALLOWED_HOSTS=127.0.0.1:8080
export LISTEN_HOST=127.0.0.1
python3 -m servarr_ui.server
```

Open `http://127.0.0.1:8080/`. Liveness is available at `GET /healthz`; it intentionally does not depend on engine availability.
