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

## Local Setup

Create an environment file:

```bash
cp .env.example .env
```

Start the stack:

```bash
docker compose up --build
```

The frontend runs on `http://localhost:3000`.
The backend runs on `http://localhost:8000`.
The backend health endpoint runs on `http://localhost:8000/api/health/`.

## Current Scope

This repository contains only the initial project structure. Business logic, models, and API endpoints are intentionally not implemented yet.
