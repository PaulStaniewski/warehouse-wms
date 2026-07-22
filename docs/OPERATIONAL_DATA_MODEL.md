# Operational Data Model

## Canonical graph

`Branch` owns locations, inventory, routes, shipments, and warehouse work. A
`DeliveryRoute` owns weekday `RouteRoundSchedule` definitions. Demand creates a
concrete `RouteRun`; its cutoff/departure values are immutable operational
snapshots. `Order`/`OrderLine` represent external commercial demand. A
`Shipment` is the outbound fulfilment document and is the authoritative owner
of the RouteRun assignment. Each current `ShipmentLine` identifies its source
`OrderLine`; an order or shipment may contain many lines and an order line may
have multiple historical or active `PickingTask` rows.

`ScannerSession`, `CartWorkSession`, `ScannerCart`, task claims and
`CartPickedItem` identify who physically picked which product, from which
location and in which cart. `PickingTask.quantity_picked` is the persisted work
aggregate; cart items are attribution evidence. The accepted control workflow
increments both `CartPickedItem.quantity_prepared` and
`PickingTask.quantity_prepared`. `PickingShortage` explains task shortfall.
`InventoryItem` is the current balance and every accepted stock mutation must
create an immutable `StockMovement`. `AuditLog` is append-only operational
evidence and is never read as a current quantity.

## Quantity ownership and invariants

- `OrderLine.quantity_ordered` and `ShipmentLine.ordered_quantity` preserve the
  imported original demand. Fulfilment does not rewrite them.
- `ShipmentLine.cancelled_quantity` and its adjustment history explain removed
  fulfilment demand. Effective quantity is original minus removed.
- Non-cancelled `PickingTask.quantity_to_pick` totals must equal effective
  quantity. Completed tasks remain history; unused work is cancelled, not
  deleted.
- Physical picked quantity is the aggregate persisted task quantity backed by
  cart-item evidence. It cannot exceed effective quantity.
- In the current accepted workflow controlled and prepared quantities share
  `PickingTask.quantity_prepared`; they cannot exceed picked quantity. A future
  distinct control checkpoint must add evidence rather than another inferred
  counter.
- Zero-effective and cancelled shipment lines never contribute active route
  workload. Cross-branch order, shipment, route, task and location links are
  invalid. Closed routes reject new assignment and automatic work.
- Invalid values raise domain validation errors. Projection code never clamps
  persisted invalid quantities to make them appear valid.

## Shared projections

`operations.operational_projections` is the backend read authority.
`shipment_line_progress` produces original, effective, removed, target, picked,
controlled, prepared, shortage and remaining quantities plus one line state and
blocking reason. `shipment_operational_projection` aggregates those line
values. `route_run_workload_projection` counts each effective line exactly once
as `unstarted`, `started`, `picked`, or `prepared`. Route readiness uses the
same prepared evidence and the shipment lifecycle. APIs expose these values;
the frontend formats them but does not recalculate business state.

## Authoritative writes

Shipment activation, task posting, preparation, cancellation, quantity
removal, route reassignment and route close use transactional shipment/route
services with branch checks and row locks. Scanner picking/control owns the
physical work write and pairs inventory decrements with `StockMovement`.
Quantity removal, reassignment and status-only commands do not mutate stock.

`operations.operational_import.upsert_external_shipment` is the internal AX-like
boundary. It idempotently resolves branch/route/product, upserts order,
shipment and lines, creates or synchronizes unstarted work, demand-creates the
eligible route round, and rejects identity or post-work quantity conflicts.
There is no live AX connection in this boundary.

## Consistency checker

Run `python manage.py check_operational_consistency`. Options include
`--branch`, `--include-closed`, `--json`, and `--fail-on-error`. It is read-only
and checks graph identity, branch boundaries, quantity equations, task targets,
terminal-route workload, projection totals, cart-item route identity and route
round uniqueness. Inventory balances have no opening-balance ledger in the
current schema, so the checker cannot infer historical movement completeness
from a current balance alone; stock-changing services are instead covered by
transactional regression tests.

## Demo ownership

Seed repair is restricted to stable demo references and route codes already
declared by `seed_demo_data`. User-created or production-like records are not
eligible for cleanup. Operational demo records should enter through the same
import, route, scanner, preparation and close services used by application
requests. Repeated seed execution must retain the same business identifiers and
must not duplicate tasks, movements, adjustments or events.



## RouteRun dispatch-board projection

active_route_run_queryset defines the shared non-terminal, non-empty RouteRun board set and authoritative order. Route Monitor presents the full set; Scanner Proformas preserves that relative order while filtering to RouteRuns with an active Shipment, effective ShipmentLine, and canonical nonterminal PickingTask with remaining quantity. Shared rows serialize identical effective ShipmentLine buckets, PickingTask quantities, active claims, readiness, cutoff/departure attention, and progress. Scanner aliases are presentation-only and never recalculate operational truth. Exact RouteRun IDs connect job selection to Shipment-owned route assignment; operational identifiers are display labels only.

## RouteRun close package

RouteRun remains the aggregate boundary for outbound route closure. route_close_readiness projects blockers from every active Shipment, effective ShipmentLine, and canonical PickingTask. The same projection supplies Shipments command eligibility, Shipments detail, Route Monitor readiness, and close-service validation.

Closing owns a single transaction: lock RouteRun and Shipments, validate readiness, print the supported Shipment-document package, write route-level print evidence to RouteRun.documents_printed_at and Event Register, then set RouteRun status to closed and complete active Shipments. Printing one Shipment separately updates only that Shipment's document evidence.