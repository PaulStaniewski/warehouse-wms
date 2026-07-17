import { Link } from "react-router-dom";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useStockAdjustments } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import type { StockMovement } from "../types/api";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

function directionLabel(direction: StockMovement["adjustment_direction"]) {
  if (direction === "increase") return "Increase";
  if (direction === "decrease") return "Decrease";
  return "Unknown";
}

function directionClass(direction: StockMovement["adjustment_direction"]) {
  if (direction === "increase") return "status-pill status-pill--ok";
  if (direction === "decrease") return "status-pill status-pill--error";
  return "status-pill status-pill--loading";
}

export function StockAdjustmentsPage() {
  const { activeBranchCode } = useActiveBranch();
  const [search, setSearch] = useState("");
  const [product, setProduct] = useState("");
  const [location, setLocation] = useState("");
  const [direction, setDirection] = useState("");
  const [performedBy, setPerformedBy] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const adjustments = useStockAdjustments({
    branch: activeBranchCode,
    dateFrom,
    dateTo,
    direction,
    location,
    page,
    performedBy,
    product,
    search,
  });

  function resetPage() {
    if (page !== 1) {
      setPage(1);
    }
  }

  return (
    <>
      <PageHeader
        title="Stock Adjustments"
        description={`Read-only adjustment history for working branch ${activeBranchCode || "-"}. Manual creation is disabled until a stock-adjustment permission is defined.`}
      />

      <section className="filter-panel">
        <label>
          <span>Search</span>
          <input
            onChange={(event) => {
              setSearch(event.target.value);
              resetPage();
            }}
            placeholder="Reference, SKU, location or worker"
            value={search}
          />
        </label>
        <label>
          <span>Product</span>
          <input
            onChange={(event) => {
              setProduct(event.target.value);
              resetPage();
            }}
            placeholder="SKU, barcode or name"
            value={product}
          />
        </label>
        <label>
          <span>Location</span>
          <input
            onChange={(event) => {
              setLocation(event.target.value);
              resetPage();
            }}
            placeholder="Example A-01-01"
            value={location}
          />
        </label>
        <label>
          <span>Direction</span>
          <select
            onChange={(event) => {
              setDirection(event.target.value);
              resetPage();
            }}
            value={direction}
          >
            <option value="">All</option>
            <option value="increase">Increase</option>
            <option value="decrease">Decrease</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
        <label>
          <span>Performed by</span>
          <input
            onChange={(event) => {
              setPerformedBy(event.target.value);
              resetPage();
            }}
            placeholder="Username"
            value={performedBy}
          />
        </label>
        <label>
          <span>Date from</span>
          <input
            onChange={(event) => {
              setDateFrom(event.target.value);
              resetPage();
            }}
            type="date"
            value={dateFrom}
          />
        </label>
        <label>
          <span>Date to</span>
          <input
            onChange={(event) => {
              setDateTo(event.target.value);
              resetPage();
            }}
            type="date"
            value={dateTo}
          />
        </label>
      </section>

      <DataState isLoading={adjustments.isLoading} isError={adjustments.isError} error={adjustments.error}>
        <DataTable<StockMovement>
          rows={adjustments.data?.results ?? []}
          emptyMessage="No stock adjustments found."
          columns={[
            {
              key: "reference",
              header: "Reference",
              render: (adjustment) => (
                <Link className="table-link mono" to={`/wms/stock-adjustments/${adjustment.id}`}>
                  {adjustment.reference || `Movement ${adjustment.id}`}
                </Link>
              ),
            },
            { key: "time", header: "Performed", render: (adjustment) => formatDateTime(adjustment.created_at) },
            { key: "branch", header: "Branch", render: (adjustment) => adjustment.branch_code },
            { key: "product", header: "Product", render: (adjustment) => <span className="mono">{adjustment.product_sku}</span> },
            { key: "location", header: "Location", render: (adjustment) => adjustment.adjustment_location_code ?? <span className="muted">-</span> },
            {
              key: "direction",
              header: "Direction",
              render: (adjustment) => (
                <span className={directionClass(adjustment.adjustment_direction)}>
                  {directionLabel(adjustment.adjustment_direction)}
                </span>
              ),
            },
            { key: "quantity", header: "Quantity", render: (adjustment) => adjustment.quantity },
            { key: "worker", header: "Performed by", render: (adjustment) => adjustment.performed_by_username ?? <span className="muted">System</span> },
            { key: "origin", header: "Origin", render: (adjustment) => adjustment.origin },
            { key: "status", header: "Status", render: (adjustment) => <span className="status-pill status-pill--ok">{adjustment.status}</span> },
          ]}
        />
        <div className="pagination-bar">
          <span>{adjustments.data?.count ?? 0} adjustments</span>
          <div>
            <button disabled={!adjustments.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">
              Previous
            </button>
            <strong>Page {page}</strong>
            <button disabled={!adjustments.data?.next} onClick={() => setPage((value) => value + 1)} type="button">
              Next
            </button>
          </div>
        </div>
      </DataState>
    </>
  );
}
