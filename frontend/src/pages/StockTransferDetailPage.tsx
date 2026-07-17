import { Link, useParams } from "react-router-dom";

import { useStockTransfer } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function StockTransferDetailPage() {
  const { id } = useParams();
  const transfer = useStockTransfer(id);

  return (
    <>
      <PageHeader
        title={transfer.data ? transfer.data.reference || `Movement ${transfer.data.id}` : "Stock transfer detail"}
        description="Read-only internal stock transfer history."
        action={<Link className="status-pill" to="/wms/stock-transfers">Back to Stock Transfers</Link>}
      />

      <DataState isLoading={transfer.isLoading} isError={transfer.isError} error={transfer.error}>
        {transfer.data && (
          <>
            <section className="detail-grid">
              <article className="detail-card">
                <span>Status</span>
                <strong><span className="status-pill status-pill--ok">{transfer.data.status}</span></strong>
              </article>
              <article className="detail-card">
                <span>Performed</span>
                <strong>{formatDateTime(transfer.data.created_at)}</strong>
              </article>
              <article className="detail-card">
                <span>Branch</span>
                <strong className="mono">{transfer.data.branch_code}</strong>
              </article>
              <article className="detail-card">
                <span>Origin</span>
                <strong>{transfer.data.origin}</strong>
              </article>
              <article className="detail-card">
                <span>Product</span>
                <strong className="mono">{transfer.data.product_sku}</strong>
                <p>{transfer.data.product_name}</p>
              </article>
              <article className="detail-card">
                <span>Quantity</span>
                <strong>{transfer.data.quantity}</strong>
              </article>
              <article className="detail-card">
                <span>Source location</span>
                <strong>
                  {transfer.data.source_location && transfer.data.source_location_code ? (
                    <Link className="table-link mono" to={`/wms/locations/${transfer.data.source_location}`}>
                      {transfer.data.source_location_code}
                    </Link>
                  ) : (
                    "Not set"
                  )}
                </strong>
              </article>
              <article className="detail-card">
                <span>Destination location</span>
                <strong>
                  {transfer.data.destination_location && transfer.data.destination_location_code ? (
                    <Link className="table-link mono" to={`/wms/locations/${transfer.data.destination_location}`}>
                      {transfer.data.destination_location_code}
                    </Link>
                  ) : (
                    "Not set"
                  )}
                </strong>
              </article>
              <article className="detail-card">
                <span>Performed by</span>
                <strong>{transfer.data.performed_by_username || "Scanner"}</strong>
              </article>
              <article className="detail-card">
                <span>Movement type</span>
                <strong>{transfer.data.movement_type_label}</strong>
              </article>
              <article className="detail-card">
                <span>History record</span>
                <strong className="mono">StockMovement #{transfer.data.id}</strong>
              </article>
              <article className="detail-card">
                <span>Events</span>
                <strong>
                  <Link className="table-link" to="/wms/events/current">
                    Open Current Events
                  </Link>
                </strong>
              </article>
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
