import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { usePickingShortageConfirmMissing, usePickingShortageFoundStock, usePickingShortages } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" });
}

export function PickingShortagesPage() {
  const { activeBranchCode } = useActiveBranch();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ actor: "", dateFrom: "", dateTo: "", location: "", product: "", search: "", status: "" });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mode, setMode] = useState<"found" | "missing">("found");
  const [quantity, setQuantity] = useState("1");
  const [locationCode, setLocationCode] = useState("");
  const [note, setNote] = useState("");
  const shortages = usePickingShortages(activeBranchCode, filters);
  const foundStock = usePickingShortageFoundStock();
  const confirmMissing = usePickingShortageConfirmMissing();
  const rows = shortages.data?.results ?? [];

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["picking-shortages"] });
    await queryClient.invalidateQueries({ queryKey: ["inventory-items"] });
    await queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] });
  }

  async function handleAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    if (mode === "found") {
      await foundStock.mutateAsync({ locationCode, note, quantity, shortageId: selectedId });
    } else {
      await confirmMissing.mutateAsync({ note, shortageId: selectedId });
    }
    setSelectedId(null);
    setLocationCode("");
    setNote("");
    setQuantity("1");
    await refresh();
  }

  return (
    <>
      <PageHeader
        title="Picking Shortages"
        description="Stock reported missing during picking and available for later warehouse investigation."
      />
      <section className="filter-panel">
        <input placeholder="Search shortage, SKU, order, cart or actor" value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} />
        <select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="found">Found</option>
          <option value="confirmed_missing">Confirmed missing</option>
        </select>
        <input placeholder="Product SKU" value={filters.product} onChange={(event) => setFilters({ ...filters, product: event.target.value })} />
        <input placeholder="Location" value={filters.location} onChange={(event) => setFilters({ ...filters, location: event.target.value })} />
        <input placeholder="Actor" value={filters.actor} onChange={(event) => setFilters({ ...filters, actor: event.target.value })} />
        <input type="date" value={filters.dateFrom} onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })} />
        <input type="date" value={filters.dateTo} onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })} />
      </section>
      <DataState isLoading={shortages.isLoading} isError={shortages.isError} error={shortages.error}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Shortage</th>
                <th>Product</th>
                <th>Location missing</th>
                <th>Alternative allocated</th>
                <th>Customer unfulfilled</th>
                <th>Expected location</th>
                <th>Alternative locations</th>
                <th>Cart</th>
                <th>Order</th>
                <th>Customer alias</th>
                <th>Reported by</th>
                <th>Reported at</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.reference}</td>
                  <td>{row.product_sku}<br /><span className="muted">{row.product_name}</span></td>
                  <td>{formatQuantity(row.location_missing_quantity ?? row.quantity)}</td>
                  <td>{formatQuantity(row.alternative_allocated_quantity ?? "0")}</td>
                  <td>{formatQuantity(row.customer_unfulfilled_quantity ?? "0")}</td>
                  <td>{row.reported_location_code}</td>
                  <td>
                    {row.allocations.length > 0
                      ? row.allocations.map((allocation) => `${allocation.location_code} x${formatQuantity(allocation.quantity)}`).join(", ")
                      : "-"}
                  </td>
                  <td>{row.cart_code ?? "-"}</td>
                  <td>{row.order_reference}</td>
                  <td>{row.customer_alias_snapshot}</td>
                  <td>{row.reported_by_worker_code || row.reported_by_username || "-"}</td>
                  <td>{formatDateTime(row.reported_at)}</td>
                  <td>{row.status_label}</td>
                  <td>
                    <button disabled={row.status !== "open"} onClick={() => { setSelectedId(row.id); setQuantity(row.unresolved_unconfirmed_quantity ?? row.unresolved_quantity); }} type="button">
                      Resolve
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={14}>No picking shortages found.</td></tr>}
            </tbody>
          </table>
        </div>
      </DataState>
      {selectedId && (
        <form className="action-panel" onSubmit={handleAction}>
          <strong>Resolve picking shortage</strong>
          <select value={mode} onChange={(event) => setMode(event.target.value as "found" | "missing")}>
            <option value="found">Stock found</option>
            <option value="missing">Confirm physical loss</option>
          </select>
          {mode === "found" && (
            <>
              <input inputMode="numeric" placeholder="Found quantity" value={quantity} onChange={(event) => setQuantity(event.target.value.replace(/\D/g, ""))} />
              <input placeholder="Actual found location" value={locationCode} onChange={(event) => setLocationCode(event.target.value)} />
            </>
          )}
          <input placeholder="Note" value={note} onChange={(event) => setNote(event.target.value)} />
          <button disabled={foundStock.isPending || confirmMissing.isPending} type="submit">
            Confirm
          </button>
        </form>
      )}
    </>
  );
}
