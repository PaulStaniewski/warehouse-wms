import { Fragment, useMemo, useState } from "react";

import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCurrentAuditLogs } from "../api/queries";
import type { AuditLog } from "../types/api";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatEvent(value: string, fallback: string) {
  return (value || fallback).replaceAll("_", " ");
}

function eventQuantity(event: AuditLog) {
  if (event.event_type === "control_mismatch" && event.checked_quantity && event.expected_quantity) {
    return `${formatQuantity(event.checked_quantity)} / ${formatQuantity(event.expected_quantity)}`;
  }
  return formatQuantity(event.checked_quantity || event.quantity);
}

function formatQuantity(value: string | null) {
  if (!value) return "";
  const numberValue = Number(value);
  return Number.isFinite(numberValue)
    ? new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 }).format(numberValue)
    : value;
}

function eventReference(event: AuditLog) {
  return (
    event.order_reference ||
    event.transfer_reference ||
    event.pallet_code ||
    event.discrepancy_reference ||
    event.route_run_label ||
    event.reference ||
    `${event.entity_name}${event.entity_id ? ` / ${event.entity_id}` : ""}`
  );
}

function EventDetails({ event }: { event: AuditLog }) {
  const details = [
    ["Event type", formatEvent(event.event_type, event.action_type)],
    ["Message", event.message],
    ["Timestamp", formatDateTime(event.created_at)],
    ["Branch", event.branch_code],
    ["Actor", event.actor_display || event.actor_username || "System"],
    ["Product", event.product_sku ? `${event.product_sku} / ${event.product_name ?? ""}` : ""],
    ["Quantity", formatQuantity(event.quantity)],
    ["Expected quantity", formatQuantity(event.expected_quantity)],
    ["Checked quantity", formatQuantity(event.checked_quantity)],
    ["From", event.source_location_code || event.source_label],
    ["To", event.destination_location_code || event.destination_label],
    ["Cart", event.cart_code],
    ["Order", event.order_reference],
    ["Route", event.route_run_label],
    ["Transfer", event.transfer_reference],
    ["Pallet", event.pallet_code],
    ["Discrepancy", event.discrepancy_reference],
    ["Related object", `${event.entity_name}${event.entity_id ? ` / ${event.entity_id}` : ""}`],
    ["Result", event.result],
  ].filter(([, value]) => Boolean(value));

  return (
    <dl className="event-detail-grid">
      {details.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CurrentEventsPage() {
  const { activeBranchCode } = useActiveBranch();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [eventType, setEventType] = useState("");
  const [product, setProduct] = useState("");
  const [cart, setCart] = useState("");
  const [location, setLocation] = useState("");
  const [order, setOrder] = useState("");
  const [actor, setActor] = useState("");
  const [result, setResult] = useState("");
  const filters = useMemo(
    () => ({ actor, cart, eventType, location, order, product, result, search }),
    [actor, cart, eventType, location, order, product, result, search],
  );
  const auditLogs = useCurrentAuditLogs(activeBranchCode, filters);
  const rows = auditLogs.data?.results ?? [];

  return (
    <>
      <PageHeader
        title="Current events"
        description="Search recent warehouse events by product, cart, location, order, route, pallet, transfer or worker."
      />

      <section className="event-filter-panel">
        <label className="event-search-field">
          <span>Search</span>
          <input
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search product, cart, location, order, route, pallet, transfer or worker"
            value={search}
          />
        </label>
        <label>
          <span>Event type</span>
          <select onChange={(event) => setEventType(event.target.value)} value={eventType}>
            <option value="">All</option>
            <option value="pick">Pick</option>
            <option value="control">Control</option>
            <option value="control_mismatch">Control mismatch</option>
            <option value="receive">Receive</option>
            <option value="receive_scan">Receive scan</option>
          </select>
        </label>
        <label>
          <span>Result</span>
          <select onChange={(event) => setResult(event.target.value)} value={result}>
            <option value="">All</option>
            <option value="passed">Passed</option>
            <option value="mismatch">Mismatch</option>
          </select>
        </label>
        <label>
          <span>Product</span>
          <input onChange={(event) => setProduct(event.target.value)} placeholder="FILTR-001" value={product} />
        </label>
        <label>
          <span>Cart</span>
          <input onChange={(event) => setCart(event.target.value)} placeholder="WOZEK-01" value={cart} />
        </label>
        <label>
          <span>Location</span>
          <input onChange={(event) => setLocation(event.target.value)} placeholder="A-01-01" value={location} />
        </label>
        <label>
          <span>Order</span>
          <input onChange={(event) => setOrder(event.target.value)} placeholder="AX-ORDER-0001" value={order} />
        </label>
        <label>
          <span>Actor</span>
          <input onChange={(event) => setActor(event.target.value)} placeholder="GDY_WORKER" value={actor} />
        </label>
      </section>

      <DataState isLoading={auditLogs.isLoading} isError={auditLogs.isError} error={auditLogs.error}>
        <div className="event-ledger-table-wrap">
          <table className="event-ledger-table">
            <thead>
              <tr>
                <th>Created</th>
                <th>Event</th>
                <th>Product</th>
                <th>Quantity</th>
                <th>From</th>
                <th>To</th>
                <th>Reference</th>
                <th>Actor</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={8}>No current events found.</td>
                </tr>
              ) : (
                rows.map((event) => {
                  const expanded = expandedId === event.id;
                  return (
                    <Fragment key={event.id}>
                      <tr
                        className="event-ledger-row"
                        onClick={() => setExpandedId(expanded ? null : event.id)}
                      >
                        <td>{formatDateTime(event.created_at)}</td>
                        <td>{formatEvent(event.event_type, event.action_type)}</td>
                        <td>{event.product_sku}</td>
                        <td>{eventQuantity(event)}</td>
                        <td>{event.source_location_code || event.source_label}</td>
                        <td>{event.destination_location_code || event.destination_label}</td>
                        <td>{eventReference(event)}</td>
                        <td>{event.actor_display || event.actor_username || "System"}</td>
                      </tr>
                      {expanded && (
                        <tr className="event-ledger-detail-row">
                          <td colSpan={8}>
                            <EventDetails event={event} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </DataState>
    </>
  );
}
