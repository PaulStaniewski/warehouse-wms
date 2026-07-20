# Critical Workflow Integration Tests

This document summarizes the cross-module workflow tests that protect the Warehouse WMS operational paths. These tests use authenticated API clients, real scanner/WMS command endpoints, database assertions, read-model endpoints, and event register checks.

## Test Location

The integration scenarios live in `backend/operations/tests.py` alongside the existing operations suite:

- `CriticalQuickTransferIntegrationTests`
- `CriticalCycleCountIntegrationTests`
- `CriticalInterBranchExactReceivingIntegrationTests`
- `CriticalInterBranchShortageIntegrationTests`
- `CriticalSourceReviewReconciliationIntegrationTests`
- `CriticalExactPickingControlIntegrationTests`
- `CriticalPickingShortageControlIntegrationTests`
- `ReturnDocumentWorkflowTests`
- `SalesCorrectionWorkflowTests`

The project still uses the existing monolithic operations test module. No test package migration was introduced for this stage.

## Covered Workflows

| Workflow | Roles | API stages covered | Read models verified |
| --- | --- | --- | --- |
| Scanner Quick Transfer | Branch Worker, unrelated branch Worker | Scanner transfer command, validation rollback, idempotent repeat submission, concurrent duplicate delivery, branch-protected detail access | Stock Movements / Stock Transfers history, Stock Movement detail, Current Events |
| Cycle Count safe variance | Branch Leader, Branch Worker, unrelated branch Leader | WMS create/open, blind scanner count/submit, Leader review, adjustment, duplicate adjustment guard, close | Cycle Count detail, Review Queue, Stock Adjustments, Current Events |
| Cycle Count recount | Branch Leader, Branch Worker | Stale original variance, recount request, blind recount detail, scanner recount submission, acceptance, adjustment, duplicate guard, stale recount rejection | Cycle Count detail, Stock Movements, Current Events |
| Inter-branch exact receiving | Destination Worker, source Worker, unrelated Worker | Destination arrival confirmation, receiving start/recovery, product scan, put-away, canonical close, compatibility close alias retry, validation rollback, branch isolation | MM Tasks, Transport Overview, Universal Contents, Current Events |
| Inter-branch shortage receiving | Destination Worker, source Worker, unrelated Worker | Destination arrival confirmation, partial receiving, shortage close, duplicate close guard, compatibility close alias retry, branch visibility | Transfer Discrepancies, Discrepancy Action Queue, Inventory Exceptions, Transport Overview, Universal Contents, Current Events |
| Source review and reconciliation | Destination Worker/Leader, source Worker/Leader, unrelated Worker | Receiving shortage, discrepancy report print, destination shortage confirmation, source review, reconciliation acknowledgement, source stock verification, partial source recovery, source search close, final manual accounting | Discrepancy detail, Source Review detail, Reconciliation detail, Source Stock Verification detail, Action Queue, Inventory Exceptions, Current Events |
| Exact outbound Picking and Control | Branch Worker, Control Worker, unrelated branch Worker | Proforma job creation, cart start, participant join, picking direction, location/product scans, quantity confirmation, control label, prepare scans, finish control, duplicate and immutable-state guards | Scanner Tasks, Route Run detail, Inventory Exceptions, Current Events |
| Picking shortage and Control | Branch Worker, Control Worker, Branch Leader, unrelated branch Worker | Partial picking, shortage challenge/report, replenishment request creation, picked-goods control, Leader-only shortage follow-up rejection for Worker, duplicate shortage replay, branch isolation | Picking Shortages, Inventory Exceptions, Route Run detail, Current Events |
| External Return Documents | Branch Worker, Branch Leader, unrelated branch Worker | Exact external-reference lookup, partial accept/reject/on-hold actions, on-hold resolution, idempotent action replay, branch isolation | Return document detail, Returns Area inventory, Stock Movements, Current Events |
| Sales Corrections | Branch Worker, Branch Leader, unrelated branch Worker | Completed-sales search, correction draft creation, line add/update/remove, confirmation, idempotent confirmation replay, over-correction guard, branch isolation | Sales Correction detail, Correction Activity Report, Returns Area inventory, Stock Movements, Current Events |

## Authorization Coverage

The integration tests include targeted authorization checks:

- anonymous users cannot start operational commands,
- Workers cannot create or reconcile Leader-owned cycle count work,
- unrelated branch users cannot read branch-owned movement/session details,
- source and unrelated branch users cannot operate destination receiving sessions,
- unrelated branch users cannot inspect or finish another branch scanner control cart,
- inter-branch discrepancy detail is visible to participating branches while unrelated branches are excluded,
- destination users cannot execute source-owned review and source-stock commands,
- source Workers can investigate source work, while final reconciliation completion remains Leader-only,
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
- exact transfer receiving creates destination inventory without a discrepancy case,
- shortage transfer receiving creates one discrepancy case with shortage lines and does not duplicate it on retry,
- exact Picking reduces source stock at physical pick time and Control does not deduct inventory again,
- Picking shortages move missing location stock to branch `UNCONFIRMED`, create replenishment when no alternative stock covers the shortage, and leave only physically picked goods available for Control,
- source review and reconciliation preserve the accounting identity for confirmed shortages,
- source stock recovery restores found stock into source inventory through `SOURCE_DISCREPANCY_RECOVERY`,
- Event Register entries for meaningful workflow transitions.
- External Return Document accepted quantities post only into the branch Returns Area; rejected and on-hold quantities do not mutate inventory.
- Sales Correction confirmation posts all draft lines atomically into the branch Returns Area and creates one `SALES_CORRECTION_RECEIPT` StockMovement per line.

## Inter-Branch Receiving Boundary

The current receiving command flow starts at destination arrival. There is no dedicated source-branch release scanner/API command in the tested path, so the critical receiving fixtures create a transfer and pallet directly in the released/in-transit database state. From that point onward the tests use the real scanner and WMS API endpoints.

## Source Review And Reconciliation

`CriticalSourceReviewReconciliationIntegrationTests` starts from a real destination receiving shortage. The baseline transfer has 9 expected units, 6 received units, and 3 missing units across two products. Destination report printing posts those missing units to destination `UNCONFIRMED`, and a destination Leader confirms the full shortage, creating exactly one source review.

The source branch starts and completes the source review with `source_shortage_found`, which creates exactly one reconciliation on the source stock verification route. A source Worker acknowledges it, starts source verification, records 1 found unit at a source location, then closes the search with 2 unresolved units. The final accounting assertion is:

```text
expected 9 = received 6 + source-found 1 + unresolved/source-loss 2
```

Found source stock is operational, not only evidential: the recovery command restores quantity to source inventory and creates one `SOURCE_DISCREPANCY_RECOVERY` stock movement. The final manual reconciliation is Leader-only and completes the case without adding destination inventory. The test verifies duplicate command safety, invalid product/location rollback, branch-scoped read models, Action Queue transitions, Inventory Exceptions transitions, and Event Register evidence for the chain.

## Outbound Picking And Control

`CriticalExactPickingControlIntegrationTests` covers the normal outbound path from deterministic route/order/task setup into real scanner APIs. The preparation boundary is the initial WMS document/task fixture: there is no separate tested WMS API for creating the source order, route run, and picking tasks, so the test creates those records directly and then uses the scanner APIs for all execution.

The exact flow creates a merged Picking Job from `/api/scanner/proformas/create-jobs/`, starts it on a cart, verifies task visibility, idempotent same-user join, participant ownership, and persisted `beginning` picking direction, then performs real location and product scans through `/api/scanner/picking/confirm-location/` and `/api/scanner/picking/pick/`. The test verifies wrong location, wrong product, over-quantity, cross-branch start/control, unrelated task substitution, duplicate final pick, inventory mutation, StockMovement evidence, and absence of Picking Shortage records.

Control is branch-scoped scanner work. A same-branch Worker can open the picked cart, print the customer label, prepare each picked product, and finish control. Control completion releases the cart, completes the Picking Job, marks tasks completed, and moves the Route Run to ready-to-close. Control does not deduct inventory again. Completed sessions reject repeated finish and further preparation attempts.

`CriticalPickingShortageControlIntegrationTests` covers the current supported shortage path. The Worker physically picks available units, records a real location shortage through the scanner challenge/report flow, and then continues with other picked goods. When no alternative stock is available, the shortage remains open and a replenishment request is created. The missing quantity is moved from the reported shelf location to branch `UNCONFIRMED`; it is not placed on the cart and is not available for Control. Control prepares only physically picked units, can finish the cart, and leaves the Picking Job in the picked state while the shortage/replenishment remain actionable.

Worker users cannot execute the Leader-only physical-loss confirmation action. Unrelated branch users cannot list the branch Picking Shortage, inspect the control cart, or use another branch scanner session. Event Register assertions cover pick, picking shortage, replenishment, and control evidence using stable event metadata rather than full prose messages.

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

## Returns And Sales Corrections

The Returns and Sales Corrections foundation uses English project terminology throughout the WMS UI and API. External references such as `ZW1103872` are treated as data supplied by an upstream system, not as module names.

External Return Documents are imported records with expected return lines. Same-branch Workers and Leaders can accept, reject, or put quantities on hold. Quantity accounting follows:

```text
expected = accepted + rejected + on hold + remaining
```

Each decision creates an append-only Return Action with the authenticated employee, timestamp, quantity, source pool, note, and optional StockMovement. On-hold quantities can later be accepted or rejected by another same-branch employee without overwriting the original history.

Sales Corrections are separate from External Return Documents. A Worker or Leader creates a draft, searches completed same-branch sales by product SKU/barcode, adds source OrderLine rows, enters returned quantities, and confirms. Confirmation validates remaining correctable quantity against completed sales and already completed correction lines. Draft lines do not consume correctable quantity.

Accepted return quantities and confirmed correction quantities are posted to the branch location with code `RETURNS` and type `returns` when present. Existing legacy return locations remain readable, and Quick Transfer is still the follow-up workflow for moving goods from the Returns Area to normal shelf locations.

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
docker compose run --rm backend python manage.py test operations.tests.CriticalQuickTransferIntegrationTests operations.tests.CriticalCycleCountIntegrationTests operations.tests.CriticalInterBranchExactReceivingIntegrationTests operations.tests.CriticalInterBranchShortageIntegrationTests operations.tests.CriticalSourceReviewReconciliationIntegrationTests --noinput
```

Run only outbound Picking and Control critical scenarios:

```powershell
docker compose run --rm backend python manage.py test operations.tests.CriticalExactPickingControlIntegrationTests operations.tests.CriticalPickingShortageControlIntegrationTests --noinput
```

Run Returns and Sales Corrections focused scenarios:

```powershell
docker compose run --rm backend python manage.py test operations.tests.ReturnDocumentWorkflowTests operations.tests.SalesCorrectionWorkflowTests --noinput
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
