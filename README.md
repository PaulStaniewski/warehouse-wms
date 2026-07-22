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

## Production Readiness

Provider-neutral production notes live in:

- `docs/PRODUCTION_READINESS.md`
- `docs/BACKUP_RESTORE.md`

Production uses Gunicorn for Django and Nginx for the built React app. The development override keeps Django `runserver` and Vite.

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

Seed deterministic demo data:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm backend python manage.py seed_demo_data
```

Useful demo references:

- External Return Document: `ZW1103872`
- Returns Area location: `RETURNS`
- Completed sales for Sales Corrections: `AX-SALE-RET-001`, `AX-SALE-RET-002`
- Shipments Command Center: `SHP-GDY-0001`, `SHP-GDY-0002`, `SHP-GDY-0003`, `SHP-GDY-0006`, `SHP-GDY-0007`, `SHP-GDY-0008`, `SHP-GDA-GDY-0001`, `SHP-GDA-GDY-0002`
- Inter-branch shipment awaiting document-only posting: `SHP-GDA-GDY-0001`
- Route Monitor and Shipments share the same RouteRun demo data. `SHP-GDY-0001` and `SHP-GDY-0002` are assigned to one incomplete route; `SHP-GDY-0003` is on a route ready to close; `SHP-GDY-0006` can be moved to today's `SHP-GDY-0007` route or the weekly `SHP-GDY-0008` route.
- Change Route defaults to today's eligible routes and can expand to the current operational week. Route changes do not require a reason.
- Dynamic route rounds are schedule-driven. `RouteRoundSchedule` defines recurring weekday route slots with cutoff, departure, dispatch wave, and round number. Shipment route changes can target an existing RouteRun or a scheduled slot; scheduled slots create the RouteRun on demand when the shipment is assigned.
- RouteRun stores cutoff/departure snapshots (`cutoff_at`, `planned_departure_at`, `dispatch_wave`, `operational_identifier`) so later schedule edits do not rewrite historical route runs. Route Monitor uses active Shipment workload and hides empty route runs from the active board.
- Scanner Proformas filters the ordered active RouteRun projection to routes with effective remaining canonical picking work. The remaining cards preserve Route Monitor relative order and identical workload/readiness/attention values, and select work only by exact RouteRun ID. Fully picked and prepared routes remain on Route Monitor but are absent from Scanner Proformas.
- Deterministic Scanner scenarios include SCANNER_UNSTARTED, SCANNER_ACTIVE_PICKING, SCANNER_PARTIAL_PICK, SCANNER_ZERO_EFFECTIVE_EXCLUDED, SCANNER_PREPARED_NOT_SELECTABLE, and SCANNER_CLOSED_ROUTE_EXCLUDED; the seed report prints their canonical RouteRun and Shipment references.
- Route Schedule Editor is available at `/wms/route-schedules` for branch Leaders. It validates maximum routes per dispatch wave and minimum departure gaps between waves.
- Shipment line quantity removal can be tested on active/unpicked demo shipments. Removed unpicked quantity is not a return: it creates history only, does not mutate inventory, does not create a StockMovement, and does not create a Sales Correction. Picked or controlled quantities are intentionally blocked from silent removal. Removing the final unpicked unit leaves a zero-effective historical line and cancels inactive picking work.

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
## Operational Data Spine

The canonical outbound graph, quantity ownership, projections, import boundary, and consistency checker are documented in [`docs/OPERATIONAL_DATA_MODEL.md`](docs/OPERATIONAL_DATA_MODEL.md).

```powershell
python manage.py check_operational_consistency --branch GDY --fail-on-error
```
### Route close and complete document package

The outbound completion sequence is Picking -> Control -> Preparation -> Close Route from Shipments -> generate and print the complete RouteRun package -> mark the RouteRun closed -> remove it from the active Route Monitor and Scanner Proformas. Route closure validates every active Shipment and effective ShipmentLine assigned to the RouteRun. Cancelled Shipments and zero-effective lines do not block closure.

Close Route and Print Package prints one supported Shipment document for every active Shipment in the RouteRun and records route-package and route-close Event Register evidence before the RouteRun leaves the active board. Printing must succeed before the close transition; failures leave the RouteRun open. Repeated close requests replay the existing result without printing again.

Print Shipment Document is separate: it prints or reprints one Shipment document and never closes a RouteRun or marks the complete route package as printed.