# Critical Workflow Integration Tests

This document summarizes the cross-module workflow tests that protect the Warehouse WMS operational paths. These tests use authenticated API clients, real scanner/WMS command endpoints, database assertions, read-model endpoints, and event register checks.

## Test Location

The integration scenarios live in `backend/operations/tests.py` alongside the existing operations suite:

- `CriticalQuickTransferIntegrationTests`
- `CriticalCycleCountIntegrationTests`

The project still uses the existing monolithic operations test module. No test package migration was introduced for this stage.

## Covered Workflows

| Workflow | Roles | API stages covered | Read models verified |
| --- | --- | --- | --- |
| Scanner Quick Transfer | Branch Worker, unrelated branch Worker | Scanner transfer command, validation rollback, idempotent repeat submission, concurrent duplicate delivery, branch-protected detail access | Stock Movements / Stock Transfers history, Stock Movement detail, Current Events |
| Cycle Count safe variance | Branch Leader, Branch Worker, unrelated branch Leader | WMS create/open, blind scanner count/submit, Leader review, adjustment, duplicate adjustment guard, close | Cycle Count detail, Review Queue, Stock Adjustments, Current Events |
| Cycle Count recount | Branch Leader, Branch Worker | Stale original variance, recount request, blind recount detail, scanner recount submission, acceptance, adjustment, duplicate guard, stale recount rejection | Cycle Count detail, Stock Movements, Current Events |

## Authorization Coverage

The integration tests include targeted authorization checks:

- anonymous users cannot start operational commands,
- Workers cannot create or reconcile Leader-owned cycle count work,
- unrelated branch users cannot read branch-owned movement/session details,
- same-branch Workers can execute scanner work,
- same-branch Leaders can execute review and reconciliation work.

The broader role matrix remains documented in `docs/AUTHORIZATION_MATRIX.md`.

## Inventory And Audit Coverage

The tests verify that successful commands update the same state visible to users:

- source and destination inventory after scanner quick transfer,
- branch-level total quantity conservation for internal transfers,
- cycle count snapshot stability,
- count-correction StockMovement creation,
- no duplicate stock mutation on repeated commands,
- no successful StockMovement or AuditLog after validation failures,
- Event Register entries for meaningful workflow transitions.

## Quick Transfer Idempotency

Scanner Quick Transfer requires a client-generated `client_operation_id` for every command. The identifier must be a UUID string with at most 64 characters. It represents one intended physical transfer and must be reused only when retrying the same payload after an uncertain response.

The Scanner UI generates the ID before submission, keeps it while the request is pending, preserves it after a failed or uncertain response, and reuses it for manual retry when source, product, destination, and quantity are unchanged. If the operator changes the payload after a failure, the UI creates a new operation ID. After a confirmed success or idempotent replay, the form resets and the next transfer receives a fresh ID.

The backend stores the operation in `ScannerQuickTransferOperation`, which has a database-level unique constraint on `client_operation_id`. The stored operation fingerprint includes:

- authenticated user,
- branch,
- product,
- source location,
- destination location,
- quantity.

Exact replay returns the original completed StockMovement response with `replayed: true`, performs no inventory mutation, and creates no second AuditLog event. Reusing the same ID for a different payload or user returns `409 Conflict`. Requests from another branch fail branch authorization before they can replay another branch's work.

The backend checks for an existing operation inside the transaction, locks inventory rows in deterministic order, rechecks the operation after locks, creates the unique operation record before mutation, updates inventory once, creates one StockMovement and one AuditLog, and links the completed operation to the movement. This is protected by `CriticalQuickTransferConcurrencyTests`.

## Existing Specialized Coverage

The existing operations suite continues to cover deeper isolated and workflow-specific behavior for:

- inter-branch pallet receiving,
- transfer discrepancies and reconciliation,
- scanner picking and control,
- picking shortages and replenishment,
- branch scoping and role checks,
- route lifecycle and archive behavior,
- seed/reset demo workflows.

These areas are not duplicated wholesale in the critical integration classes. Future critical scenarios can be promoted from the specialized tests when a smaller cross-module contract needs explicit protection.

## Commands

Run only the critical integration scenarios:

```powershell
docker compose run --rm backend python manage.py test operations.tests.CriticalQuickTransferIntegrationTests operations.tests.CriticalCycleCountIntegrationTests --noinput
```

Run the full backend suites:

```powershell
docker compose run --rm backend python manage.py test operations --noinput
docker compose run --rm backend python manage.py test accounts --noinput
```

Run normal verification:

```powershell
docker compose run --rm backend python manage.py check
docker compose run --rm backend python manage.py makemigrations --check --dry-run
npm.cmd test
npm.cmd run build
```

## Manual Smoke Areas

Manual smoke testing is still useful for scanner ergonomics and multi-screen behavior:

- collaborative cart picking with more than one browser,
- phone camera barcode scanning,
- full inter-branch discrepancy investigation with real operator timing,
- wall monitor refresh behavior during live scanner execution.
