# Production Readiness

This project is deployable with Docker Compose or an equivalent container platform without choosing a specific cloud provider.

## Architecture

Production uses:

- `frontend`: Nginx serving the Vite build and proxying `/api/*` to Django.
- `backend`: Django served by Gunicorn on an internal container port.
- `postgres`: PostgreSQL with a durable named volume.
- `redis`: available for cache/throttle-ready deployments; currently optional for readiness.

Only the frontend publishes a host port in the production Compose layout. PostgreSQL, Redis, and the backend stay internal.

## Development

Use the development override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Development keeps:

- Django `runserver` on port `8000`,
- Vite dev server on port `3000`,
- PostgreSQL on port `5432`,
- Redis on port `6379`,
- source bind mounts and HMR.

## Production Configuration

Use a real environment file based on `.env.production.example`.

Required production values:

- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

Recommended values:

- `DJANGO_SESSION_COOKIE_SECURE=True`
- `DJANGO_CSRF_COOKIE_SECURE=True`
- `DJANGO_USE_X_FORWARDED_PROTO=True`
- `DJANGO_ENABLE_API_DOCS=False`
- `POSTGRES_CONN_MAX_AGE=60`

`DJANGO_SECRET_KEY` must be long, random, stable, and never committed. The example value `change-me` is accepted only when `DJANGO_DEBUG=True`.

## Hosts, CORS, And CSRF

`DJANGO_ALLOWED_HOSTS` is an exact comma-separated host list. Do not include URL schemes.

Production is designed as same-origin:

- browser loads the frontend from the public origin,
- Nginx proxies `/api/*` to Django,
- CORS can usually remain empty.

If a cross-origin frontend is deployed, set `CORS_ALLOWED_ORIGINS` to exact origins and keep `CORS_ALLOW_CREDENTIALS=True`.

The app uses Django session cookies. `/api/auth/session/` sets the CSRF cookie, and frontend Axios sends it as `X-CSRFToken`. Login, logout, and session-authenticated API writes keep CSRF protection enabled.

## HTTPS And Proxy Headers

TLS should terminate at the public reverse proxy or load balancer. The production container expects:

```text
X-Forwarded-Proto: https
```

Set `DJANGO_USE_X_FORWARDED_PROTO=True` only when the backend is not directly exposed to arbitrary internet clients. `DJANGO_SECURE_SSL_REDIRECT` defaults to `False` to avoid proxy redirect loops; enable it only after validating TLS termination.

HSTS is configurable through `DJANGO_SECURE_HSTS_SECONDS`. It defaults to `0`. Do not enable preload casually.

## API Docs

Swagger and schema endpoints are enabled in development and disabled in production by default through `DJANGO_ENABLE_API_DOCS=False`.

## Security Headers

Django enables:

- `SECURE_CONTENT_TYPE_NOSNIFF=True`
- `X_FRAME_OPTIONS=DENY`
- `SECURE_REFERRER_POLICY=same-origin`

A strict Content Security Policy is intentionally deferred because scanner camera/barcode features need careful testing with blob/media permissions and route-level chunks.

## Production Commands

Validate Compose:

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml config
```

Build:

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml build
```

Run migrations as a one-off release step:

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml run --rm backend python manage.py migrate
```

Start:

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Do not run `seed_demo_data` in production.

## Health Checks

Public minimal endpoints:

- `/api/health/`
- `/api/health/live/`
- `/api/health/ready/`

Liveness returns only process status. Readiness verifies database access and optionally Redis when `DJANGO_READINESS_CHECK_REDIS=True`.

## Logging

Application logs go to stdout/stderr with timestamp, severity, logger name, and message. Django still logs exceptions server-side when `DEBUG=False`, while clients receive generic error responses.

Do not log passwords, session cookies, CSRF tokens, Authorization headers, database credentials, or secret environment values.

## Deployment Check

Run:

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml run --rm backend python manage.py check --deploy
```

Remaining warnings are acceptable only when explicitly explained for the deployment, for example when TLS redirect/HSTS are intentionally controlled at an external proxy during rollout.

## Remaining Risks

- CSP is deferred until camera/barcode behavior is tested under HTTPS.
- PostgreSQL PITR/WAL archiving is not implemented in this stage.
- External secret-store integration is provider-specific and intentionally omitted.
- Browser camera scanning requires HTTPS in real production, except localhost.
