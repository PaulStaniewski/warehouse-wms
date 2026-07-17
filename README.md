# Warehouse WMS

Portfolio Warehouse Management System built with Django, React, PostgreSQL, Redis, and Docker.

## Project Structure

```text
warehouse-wms/
├── backend/
├── frontend/
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

## Backend

- Django 5 project in `backend/`
- Apps: `accounts`, `warehouse`, `operations`
- PostgreSQL configuration through environment variables
- Redis URL configured for future background and cache work

## Frontend

- React, TypeScript, and Vite application in `frontend/`
- Minimal placeholder UI only

## Docker Services

- `postgres`
- `redis`
- `backend`
- `frontend`

## Local Development With Docker

Create an environment file:

```bash
cp .env.example .env
```

Start the development stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

After the first build, normal source edits usually do not need another image build:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Development URLs:

- WMS: `http://localhost:3000/wms`
- Scanner: `http://localhost:3000/scanner`
- API through Vite proxy: `http://localhost:3000/api/health/`
- Backend direct: `http://localhost:8000/api/health/`
- Django admin: `http://localhost:8000/admin/`

The development override runs Vite on port `3000` and Django `runserver` on port `8000`.
Frontend requests to `/api/*` are proxied by Vite to the Docker backend service.

### Development Commands

Stop the stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

View logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f frontend
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f backend
```

Rebuild one service after dependency or Dockerfile changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend
docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
```

Run Django migrations:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python manage.py migrate
```

Create Django migrations:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python manage.py makemigrations
```

Open service shells:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm frontend sh
```

### Hot Reload Behavior

- Frontend `.tsx`, `.ts`, and `.css` edits are bind-mounted into the Vite container and update through HMR.
- `frontend/node_modules` is protected by the `frontend_node_modules` Docker volume so Windows host files do not replace Linux container dependencies.
- Backend `.py` edits are bind-mounted into the Django container and reload through Django `runserver`.
- Database data is stored in the `postgres_data` Docker volume and persists across container restarts.

No image rebuild is normally needed for ordinary frontend or backend source edits.

Rebuild when changing:

- Dockerfiles
- `frontend/package.json` or `frontend/package-lock.json`
- `backend/requirements.txt`
- Node, Python, or system package versions

After frontend dependency changes, rebuild the frontend image:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend
```

### Windows Docker Desktop Troubleshooting

- If HMR does not detect changes, confirm the dev override is used. It sets `VITE_USE_POLLING=true`, and `vite.config.ts` enables polling only for that mode.
- If dependencies look stale, rebuild the frontend service instead of deleting host files: `docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend`.
- If port `3000`, `8000`, `5432`, or `6379` is already in use, stop the conflicting local process or adjust the Compose port mapping.
- If bind mounts feel slow on Windows, keep the project inside a Docker Desktop-friendly filesystem location and restart only the affected service.
- Restart one service without rebuilding:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart frontend
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart backend
```

## Current Scope

This repository is a portfolio WMS application with Django APIs, React WMS screens, Scanner workflows, and Docker-based local development.
