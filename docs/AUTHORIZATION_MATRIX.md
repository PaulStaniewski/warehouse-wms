# Authorization Matrix

This document describes the implemented Warehouse WMS authorization model. Frontend checks are user-experience controls only. The backend is the authoritative security boundary.

## Role Model

| User state | Backend behavior | Frontend behavior |
| --- | --- | --- |
| Unauthenticated | May access only explicitly public endpoints such as health, authentication/session, schema, and documentation. Operational API endpoints require authentication by default. | Redirects protected WMS and Scanner routes to Login with intended destination preserved. |
| Authenticated without branch membership | Has no branch-scoped operational access. | Shows interface unavailable state. |
| Worker | Can read and execute worker workflows for assigned branches. Cannot execute Leader-only commands. | Sees normal worker WMS/Scanner navigation; Leader-only links and actions are hidden. |
| Leader | Can read worker surfaces and execute Leader-only commands for assigned branches. | Sees Leader-only navigation and action controls for the active branch. |
| Other-branch member | Cannot access records or commands owned only by unrelated branches. | Active branch selector only contains memberships returned by the backend. |
| Superuser | Treated as Leader for all active branches by backend helper functions. | Receives synthetic Leader memberships for all active branches. |
| Staff only | Staff status alone does not grant operational branch access. | Uses the same membership-driven interface access as other users. |

## Public Endpoints

| Endpoint | Access |
| --- | --- |
| `GET /api/health/` | Public health check. |
| `GET /api/auth/session/` | Public session probe; returns unauthenticated state when no session exists. |
| `POST /api/auth/login/` | Public login endpoint. |
| `POST /api/auth/logout/` | Public logout endpoint; safe when no session exists. |
| `GET /api/schema/` | Public OpenAPI schema. |
| `GET /api/docs/` | Public Swagger UI. |
| `GET /api/me/branch-memberships/` | Returns memberships only for an authenticated session; unauthenticated callers receive an authentication error response. |

The global DRF default permission is `IsAuthenticated`. Public endpoints must opt in to anonymous access explicitly with `AllowAny`. Operational viewsets, APIViews, scanner endpoints, and custom actions should not declare `AllowAny` unless the exception is documented here.

Authenticated operational surfaces include:

| Endpoint area | Access |
| --- | --- |
| `/api/stock-movements/` and `/api/stock-adjustments/` | Authenticated; branch scoped. |
| `/api/cycle-counts/` | Authenticated; branch scoped. |
| `/api/cycle-count-review-queue/` | Authenticated Leader-only branch queue. |
| `/api/inventory-exceptions/` | Authenticated; branch scoped and role filtered. |
| `/api/transport-overview/` | Authenticated; branch scoped. |
| `/api/scanner/cycle-counts/` and `/api/scanner/cycle-count-recounts/` | Authenticated; branch scoped scanner execution. |

Other operational routes inherit the authenticated default and then apply their branch, role, object, and workflow-state checks.

## Branch Scoping

| Domain | Read/detail scope | Command scope |
| --- | --- | --- |
| Branches | User memberships; superuser sees all active branches through membership API. | Branch administration is not exposed through WMS API commands. |
| Products | Authenticated reference data. Product quantities remain branch-owned through inventory endpoints. | Product mutation is not exposed in the WMS API. |
| Locations | Filtered by branch membership. Branch query parameters must match an allowed branch. | Location-changing commands validate the location branch server-side. |
| Inventory items and location contents | Filtered by branch membership. | Stock-changing commands derive branch, product, and location from stored records and validate branch access. |
| Orders, order lines, proformas, route runs | Filtered through owning branch or route branch. | Scanner and route commands validate route, order, task, and branch relationships. |
| Shipments | Filtered by owning Shipment branch. Detail access through guessed IDs is constrained by the scoped queryset. Route Monitor aggregates assigned Shipments through the authoritative RouteRun relation. Route-change targets are server-filtered by branch, state, date scope, and current route exclusion. | Same-branch Workers and Leaders may activate, post picking lists, prepare, cancel eligible shipments, print/post supported documents, remove unpicked Shipment Line quantity, change eligible routes without providing a reason, close ready routes, and use controlled status changes. Post Documents is document-only and does not release freight or receiving visibility. Quantity removal is not a return and does not create inventory, StockMovement, Picking Shortage, or Sales Correction side effects. |
| Stock movements | Filtered by movement branch. | Created by controlled workflow commands; client-provided before/after values are not authoritative. |
| Stock adjustments | Branch-scoped register and detail. | Manual creation requires Leader in the target branch. Product and location must belong to that branch. |
| External Return Documents | Filtered by document branch. Exact external-reference lookup does not leak another branch's document. | Same-branch Workers and Leaders may accept, reject, put on hold, and resolve on-hold quantities. The backend derives employee, branch, product, Returns Area, inventory before/after, and StockMovement. |
| Sales Corrections | Filtered by correction branch. Sales history search returns only completed same-branch sales with remaining correctable quantity. | Same-branch Workers and Leaders may create drafts, add source sales lines, edit draft quantities, remove draft lines, and confirm. The backend derives customer/source sale/product from the selected OrderLine and posts only to the branch Returns Area. |
| Cycle Counts | Sessions, lines, and recounts are branch-scoped. | Create/open/reconcile/recount request/recount accept/recount cancel/close are Leader-only. Scanner counting and recount execution are branch Worker-capable. |
| Picking, control, and cart work | Visible only for assigned branch work. | Workers and Leaders may execute scanner work in their branch. Task/cart/session IDs must belong to the same authorized workflow. |
| Receiving and pallet arrivals | Destination branch receives arrival/receiving work. Source branch access is not enough for destination receipt. | Receiving commands validate transfer, pallet, product, destination location, and session relationships. |
| Inter-branch transfers and transit | Source and destination branches may have legitimate visibility depending on workflow stage. Unrelated branches receive no data. | Source-only and destination-only commands validate the relevant branch role server-side. |
| Transfer discrepancies | Destination branch owns receipt discrepancy confirmation. Source branch owns source review and source stock verification stages. Reconciliation/transit visibility follows participating branch rules. | Leader-only final or administrative actions require a Leader role in the relevant participating branch. |
| Inventory Exceptions and Action Queue | Filtered to authorized branch work. Leader-only action rows are hidden from Workers by backend filtering. | Worker navigation can show read/worker queues; Leader-only action controls are hidden. |
| Event Register and AuditLog | Events are filtered by branch visibility and archive date ranges. Detail access uses the filtered queryset. | Event pages are read-only. Related links are rendered from authorized API data only. |

## WMS Frontend Permissions

| Area | Worker | Leader |
| --- | --- | --- |
| Dashboard, orders, inventory, products, locations, routes monitor, routes archive, event register | Read | Read |
| Shipments | Read plus eligible command actions for same-branch operational shipments, including safe unpicked line-quantity removal | Same as Worker for the current command-center foundation |
| Transport overview, transit, discrepancies, source reviews, reconciliations, replenishment, inventory exceptions, picking shortages | Read/allowed workflow visibility | Read plus eligible Leader actions where exposed |
| Stock transfers | Read and workflow visibility according to branch involvement | Read and eligible branch actions |
| Stock adjustments | Read; no manual create action | Read; manual create action |
| Returns | Read and process External Return Documents | Read and process External Return Documents |
| Sales Corrections | Create drafts, search completed sales, confirm corrections, view activity report | Same as Worker; no approval step |
| Cycle Counts | Read and scanner execution | Create/open/reconcile/recount/close actions |
| Cycle Count Review Queue | Hidden | Visible |

## Scanner Permissions

| Scanner module | Worker | Leader |
| --- | --- | --- |
| Proformas, Tasks, Picking, Control | Allowed for active branch work | Allowed |
| Receiving, Pallet Arrivals | Allowed for destination branch work | Allowed |
| Product, Contents, Location lookup | Allowed for active branch visibility | Allowed |
| Quick Transfer | Allowed where current backend workflow permits branch access | Allowed |
| Cycle Counts, Recounts | Count/recount execution | Count/recount execution plus WMS management actions |

## Error Semantics

| Situation | Expected response |
| --- | --- |
| Missing/invalid authentication | `401` or `403` depending on the active DRF authentication class. |
| Authenticated but role/branch forbidden command | `403`. |
| Inaccessible object detail through a scoped queryset | `404` where object existence should not be disclosed. |
| Workflow state conflict | Validation response or existing workflow conflict response. |
| Archive events without date range | `400`. |

## Implementation Notes

- `require_branch_access` is the standard branch membership check.
- `leader_required=True` is used for Leader-only commands.
- Returns and Sales Corrections intentionally do not use a Leader approval workflow. Worker and Leader authorization is identical for same-branch return/correction operations; accountability is recorded through authenticated employee attribution.
- Shipments command actions derive actor and branch from authenticated backend state. The frontend command panel is convenience only; backend checks block wrong-branch shipments, target route substitution, destination-side document posting, line/shipment parent substitution, picked-quantity removal, final-state modification, and workflow-bypassing manual status changes.
- `branch_codes_filter`, `branch_ids_filter`, `filter_branch_queryset`, and `filter_dual_branch_queryset` are used to constrain list/detail querysets.
- Superusers are treated as Leaders across all branches by `membership_role`.
- Staff users do not receive a bypass unless they are also superusers or have branch memberships.
- The active branch stored by the frontend selects working context only; it does not grant backend access.
- The DRF default permission is authenticated access. Anonymous operational access requires an explicit, documented public exception.
