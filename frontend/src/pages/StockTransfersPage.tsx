import { Link } from "react-router-dom";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useStockTransfers } from "../api/queries";
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

export function StockTransfersPage() {
  const { activeBranchCode } = useActiveBranch();
  const [search, setSearch] = useState("");
  const [product, setProduct] = useState("");
  const [sourceLocation, setSourceLocation] = useState("");
  const [destinationLocation, setDestinationLocation] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const transfers = useStockTransfers({
    branch: activeBranchCode,
    dateFrom,
    dateTo,
    destinationLocation,
    page,
    product,
    search,
    sourceLocation,
  });

  function resetPage() {
    if (page !== 1) {
      setPage(1);
    }
  }

  return (
    <>
      <PageHeader
        title="Stock Transfers"
        description={`Completed internal stock transfers for working branch ${activeBranchCode || "-"}.`}
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
          <span>Source location</span>
          <input
            onChange={(event) => {
              setSourceLocation(event.target.value);
              resetPage();
            }}
            placeholder="Example A-01-01"
            value={sourceLocation}
          />
        </label>
        <label>
          <span>Destination location</span>
          <input
            onChange={(event) => {
              setDestinationLocation(event.target.value);
              resetPage();
            }}
            placeholder="Example A-02-01"
            value={destinationLocation}
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

      <DataState isLoading={transfers.isLoading} isError={transfers.isError} error={transfers.error}>
        <DataTable<StockMovement>
          rows={transfers.data?.results ?? []}
          emptyMessage="No internal stock transfers found."
          columns={[
            {
              key: "reference",
              header: "Reference",
              render: (transfer) => (
                <Link className="table-link mono" to={`/wms/stock-transfers/${transfer.id}`}>
                  {transfer.reference || `Movement ${transfer.id}`}
                </Link>
              ),
            },
            { key: "time", header: "Performed", render: (transfer) => formatDateTime(transfer.created_at) },
            { key: "branch", header: "Branch", render: (transfer) => transfer.branch_code },
            { key: "product", header: "Product", render: (transfer) => <span className="mono">{transfer.product_sku}</span> },
            { key: "quantity", header: "Quantity", render: (transfer) => transfer.quantity },
            { key: "source", header: "Source", render: (transfer) => transfer.source_location_code ?? <span className="muted">-</span> },
            { key: "destination", header: "Destination", render: (transfer) => transfer.destination_location_code ?? <span className="muted">-</span> },
            { key: "worker", header: "Performed by", render: (transfer) => transfer.performed_by_username ?? <span className="muted">Scanner</span> },
            { key: "origin", header: "Origin", render: (transfer) => transfer.origin },
            { key: "status", header: "Status", render: (transfer) => <span className="status-pill status-pill--ok">{transfer.status}</span> },
          ]}
        />
        <div className="pagination-bar">
          <span>{transfers.data?.count ?? 0} transfers</span>
          <div>
            <button disabled={!transfers.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">
              Previous
            </button>
            <strong>Page {page}</strong>
            <button disabled={!transfers.data?.next} onClick={() => setPage((value) => value + 1)} type="button">
              Next
            </button>
          </div>
        </div>
      </DataState>
    </>
  );
}
