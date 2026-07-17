import { ArrowRight, RefreshCw, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useArchiveAuditLogs, useCurrentAuditLogs } from "../api/queries";
import type { CurrentEventFilters } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { AuditLog } from "../types/api";

type EventRegisterPageProps = {
  source: "current" | "archive";
};

const eventTypes = [
  ["", "All event types"],
  ["pick", "Pick"],
  ["control", "Control"],
  ["control_mismatch", "Control mismatch"],
  ["receive", "Receive"],
  ["receive_scan", "Receive scan"],
  ["inter_branch_arrival", "Inter-branch arrival"],
  ["stock_adjustment_created", "Stock adjustment"],
  ["cycle_count_awaiting_review", "Cycle Count review"],
  ["cycle_count_closed", "Cycle Count closed"],
];

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function eventReference(event: AuditLog) {
  return (
    event.reference ||
    event.order_reference ||
    event.transfer_reference ||
    event.pallet_code ||
    event.discrepancy_reference ||
    event.route_run_label ||
    `${event.entity_name}${event.entity_id ? ` / ${event.entity_id}` : ""}`
  );
}

function eventLocation(event: AuditLog) {
  const source = event.source_location_code || event.source_label;
  const destination = event.destination_location_code || event.destination_label;
  if (source && destination) return `${source} to ${destination}`;
  return source || destination || "-";
}

function valueFromParams(params: URLSearchParams, key: string) {
  return params.get(key) ?? "";
}

function pageFromParams(params: URLSearchParams) {
  const page = Number(params.get("page") ?? "1");
  return Number.isFinite(page) && page > 1 ? page : 1;
}

function filterFromParams(params: URLSearchParams): CurrentEventFilters {
  return {
    actor: valueFromParams(params, "actor"),
    cart: valueFromParams(params, "cart"),
    dateFrom: valueFromParams(params, "date_from"),
    dateTo: valueFromParams(params, "date_to"),
    eventType: valueFromParams(params, "event_type"),
    location: valueFromParams(params, "location"),
    order: valueFromParams(params, "order"),
    page: pageFromParams(params),
    product: valueFromParams(params, "product"),
    result: valueFromParams(params, "result"),
    search: valueFromParams(params, "search"),
  };
}

export function EventRegisterPage({ source }: EventRegisterPageProps) {
  const { activeBranch, activeBranchCode, isLoading: branchLoading } = useActiveBranch();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filterFromParams(searchParams);
  const currentEvents = useCurrentAuditLogs(activeBranchCode, filters);
  const archiveEvents = useArchiveAuditLogs(activeBranchCode, filters);
  const query = source === "current" ? currentEvents : archiveEvents;
  const rows = query.data?.results ?? [];
  const archiveReady = Boolean(filters.dateFrom && filters.dateTo);

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.delete("page");
    setSearchParams(next);
  }

  function setPage(page: number) {
    const next = new URLSearchParams(searchParams);
    if (page > 1) next.set("page", String(page));
    else next.delete("page");
    setSearchParams(next);
  }

  if (branchLoading) {
    return <div className="state-box">Loading branch context...</div>;
  }

  if (!activeBranchCode || !activeBranch) {
    return <div className="state-box state-box--error">No active branch is available for this account.</div>;
  }

  return (
    <>
      <PageHeader
        title="Event Register"
        description={`Current shows recent audit events from the last 30 days. Archive requires a date range for historical lookup. Active branch: ${activeBranch.code} / ${activeBranch.name}.`}
        action={
          <button className="dashboard-metric-retry" onClick={() => void query.refetch()} type="button">
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />

      <nav aria-label="Event register views" className="event-register-tabs">
        <Link className={source === "current" ? "active" : ""} to={`/wms/events/current?${searchParams.toString()}`}>
          Current
        </Link>
        <Link className={source === "archive" ? "active" : ""} to={`/wms/events/archive?${searchParams.toString()}`}>
          Archive
        </Link>
      </nav>

      <section className="event-filter-panel">
        <label className="event-search-field">
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => updateFilter("search", event.target.value)}
              placeholder="Reference, product, location, route, transfer, pallet, worker"
              value={filters.search ?? ""}
            />
          </div>
        </label>
        <label>
          <span>Event type</span>
          <select onChange={(event) => updateFilter("event_type", event.target.value)} value={filters.eventType ?? ""}>
            {eventTypes.map(([value, label]) => (
              <option key={value || "all"} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Actor</span>
          <input onChange={(event) => updateFilter("actor", event.target.value)} placeholder="GDY_WORKER" value={filters.actor ?? ""} />
        </label>
        <label>
          <span>Product</span>
          <input onChange={(event) => updateFilter("product", event.target.value)} placeholder="FILTR-001" value={filters.product ?? ""} />
        </label>
        <label>
          <span>Location</span>
          <input onChange={(event) => updateFilter("location", event.target.value)} placeholder="A-01-01" value={filters.location ?? ""} />
        </label>
        <label>
          <span>Order</span>
          <input onChange={(event) => updateFilter("order", event.target.value)} placeholder="AX-ORDER-0001" value={filters.order ?? ""} />
        </label>
        <label>
          <span>Cart</span>
          <input onChange={(event) => updateFilter("cart", event.target.value)} placeholder="WOZEK-01" value={filters.cart ?? ""} />
        </label>
        <label>
          <span>Result</span>
          <select onChange={(event) => updateFilter("result", event.target.value)} value={filters.result ?? ""}>
            <option value="">All results</option>
            <option value="passed">Passed</option>
            <option value="mismatch">Mismatch</option>
          </select>
        </label>
        <label>
          <span>Date from</span>
          <input onChange={(event) => updateFilter("date_from", event.target.value)} type="date" value={filters.dateFrom ?? ""} />
        </label>
        <label>
          <span>Date to</span>
          <input onChange={(event) => updateFilter("date_to", event.target.value)} type="date" value={filters.dateTo ?? ""} />
        </label>
      </section>

      {source === "archive" && !archiveReady ? (
        <div className="state-box">Select both dates to load archived events.</div>
      ) : (
        <DataState isLoading={query.isLoading} isError={query.isError} error={query.error}>
          <section className="event-register-panel">
            <div className="event-register-count">
              <strong>{query.data?.count ?? 0}</strong>
              <span>{source === "current" ? "current events" : "archived events"}</span>
            </div>
            <div className="event-ledger-table-wrap">
              <table className="event-ledger-table">
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Source</th>
                    <th>Category</th>
                    <th>Event</th>
                    <th>Actor</th>
                    <th>Reference</th>
                    <th>Location</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={8}>{source === "current" ? "No current events were found for the selected branch." : "No archived events were found for the selected filters."}</td>
                    </tr>
                  ) : (
                    rows.map((event) => (
                      <tr key={event.id}>
                        <td>{formatDateTime(event.created_at)}</td>
                        <td><span className="event-source-pill">{event.source}</span></td>
                        <td>{event.event_category}</td>
                        <td>{event.event_type_label}</td>
                        <td>{event.actor_display || event.actor_username || "System"}</td>
                        <td>{eventReference(event)}</td>
                        <td>{eventLocation(event)}</td>
                        <td>
                          <Link className="table-link" to={`/wms/events/${event.source}/${event.id}`}>
                            Open
                          </Link>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div className="event-register-pagination">
              <button disabled={!query.data?.previous} onClick={() => setPage((filters.page ?? 1) - 1)} type="button">
                Previous
              </button>
              <span>Page {filters.page ?? 1}</span>
              <button disabled={!query.data?.next} onClick={() => setPage((filters.page ?? 1) + 1)} type="button">
                Next
              </button>
            </div>
          </section>
        </DataState>
      )}
    </>
  );
}
