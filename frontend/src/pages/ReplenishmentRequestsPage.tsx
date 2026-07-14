import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useMarkReplenishmentOrderedManually, useReplenishmentRequests } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" });
}

export function ReplenishmentRequestsPage() {
  const { activeBranchCode } = useActiveBranch();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ customerAlias: "", order: "", product: "", search: "", status: "" });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [externalReference, setExternalReference] = useState("");
  const [note, setNote] = useState("");
  const requests = useReplenishmentRequests(activeBranchCode, filters);
  const markOrdered = useMarkReplenishmentOrderedManually();
  const rows = requests.data?.results ?? [];

  async function handleMarkOrdered(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    await markOrdered.mutateAsync({ externalReference, note, requestId: selectedId });
    setSelectedId(null);
    setExternalReference("");
    setNote("");
    await queryClient.invalidateQueries({ queryKey: ["replenishment-requests"] });
    await queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] });
  }

  return (
    <>
      <PageHeader
        title="Replenishment Requests"
        description="Customer replenishment requests created from warehouse picking shortages."
      />
      <section className="filter-panel">
        <input placeholder="Search reference, customer, SKU, order, cart or actor" value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} />
        <select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>
          <option value="">All statuses</option>
          <option value="pending_order">Pending order</option>
          <option value="ordered_manually">Ordered manually</option>
          <option value="exported_to_ax">Exported to AX</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <input placeholder="Product SKU" value={filters.product} onChange={(event) => setFilters({ ...filters, product: event.target.value })} />
        <input placeholder="Customer alias" value={filters.customerAlias} onChange={(event) => setFilters({ ...filters, customerAlias: event.target.value })} />
        <input placeholder="Order" value={filters.order} onChange={(event) => setFilters({ ...filters, order: event.target.value })} />
      </section>
      <DataState isLoading={requests.isLoading} isError={requests.isError} error={requests.error}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Reference</th>
                <th>Customer alias</th>
                <th>Product</th>
                <th>Quantity</th>
                <th>Order</th>
                <th>Branch</th>
                <th>Reason</th>
                <th>Status</th>
                <th>Reported by</th>
                <th>Created</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.reference}</td>
                  <td>{row.customer_alias}</td>
                  <td>{row.product_sku}<br /><span className="muted">{row.product_name}</span></td>
                  <td>{formatQuantity(row.quantity)}</td>
                  <td>{row.order_reference}</td>
                  <td>{row.branch_code}</td>
                  <td>{row.reason_label}</td>
                  <td>{row.status_label}</td>
                  <td>{row.reported_by_worker_code || "-"}</td>
                  <td>{formatDateTime(row.created_at)}</td>
                  <td>
                    <button disabled={row.status !== "pending_order"} onClick={() => setSelectedId(row.id)} type="button">
                      Mark ordered
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={11}>No replenishment requests found.</td></tr>}
            </tbody>
          </table>
        </div>
      </DataState>
      {selectedId && (
        <form className="action-panel" onSubmit={handleMarkOrdered}>
          <strong>Mark request as ordered manually</strong>
          <input placeholder="Manual or AX order reference" value={externalReference} onChange={(event) => setExternalReference(event.target.value)} />
          <input placeholder="Note" value={note} onChange={(event) => setNote(event.target.value)} />
          <button disabled={markOrdered.isPending} type="submit">Confirm ordered manually</button>
        </form>
      )}
    </>
  );
}
