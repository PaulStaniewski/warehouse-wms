import { Link, useParams } from "react-router-dom";

import { useStockAdjustment } from "../api/queries";
import { DataState } from "../components/DataState";
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

export function StockAdjustmentDetailPage() {
  const { id } = useParams();
  const adjustment = useStockAdjustment(id);

  return (
    <>
      <PageHeader
        title={adjustment.data ? adjustment.data.reference || `Movement ${adjustment.data.id}` : "Stock adjustment detail"}
        description="Read-only manual correction history. Completed adjustments cannot be edited or deleted."
        action={<Link className="status-pill" to="/wms/stock-adjustments">Back to Stock Adjustments</Link>}
      />

      <DataState isLoading={adjustment.isLoading} isError={adjustment.isError} error={adjustment.error}>
        {adjustment.data && (
          <section className="detail-grid">
            <article className="detail-card">
              <span>Status</span>
              <strong><span className="status-pill status-pill--ok">{adjustment.data.status}</span></strong>
            </article>
            <article className="detail-card">
              <span>Direction</span>
              <strong>
                <span className={directionClass(adjustment.data.adjustment_direction)}>
                  {directionLabel(adjustment.data.adjustment_direction)}
                </span>
              </strong>
            </article>
            <article className="detail-card">
              <span>Adjusted quantity</span>
              <strong>{adjustment.data.quantity}</strong>
            </article>
            <article className="detail-card">
              <span>Performed</span>
              <strong>{formatDateTime(adjustment.data.created_at)}</strong>
            </article>
            <article className="detail-card">
              <span>Branch</span>
              <strong className="mono">{adjustment.data.branch_code}</strong>
            </article>
            <article className="detail-card">
              <span>Product</span>
              <strong className="mono">{adjustment.data.product_sku}</strong>
              <p>{adjustment.data.product_name}</p>
            </article>
            <article className="detail-card">
              <span>Location</span>
              <strong>
                {adjustment.data.adjustment_location && adjustment.data.adjustment_location_code ? (
                  <Link className="table-link mono" to={`/wms/locations/${adjustment.data.adjustment_location}`}>
                    {adjustment.data.adjustment_location_code}
                  </Link>
                ) : (
                  "Not recorded"
                )}
              </strong>
            </article>
            <article className="detail-card">
              <span>Performed by</span>
              <strong>{adjustment.data.performed_by_username || "System"}</strong>
            </article>
            <article className="detail-card">
              <span>Movement type</span>
              <strong>{adjustment.data.movement_type_label}</strong>
            </article>
            <article className="detail-card">
              <span>Origin</span>
              <strong>{adjustment.data.origin}</strong>
            </article>
            <article className="detail-card">
              <span>Reference</span>
              <strong className="mono">{adjustment.data.reference || "-"}</strong>
            </article>
            <article className="detail-card">
              <span>History record</span>
              <strong className="mono">StockMovement #{adjustment.data.id}</strong>
            </article>
            <article className="detail-card">
              <span>Quantity before</span>
              <strong>Not recorded</strong>
            </article>
            <article className="detail-card">
              <span>Quantity after</span>
              <strong>Not recorded</strong>
            </article>
            <article className="detail-card">
              <span>Reason</span>
              <strong>Not recorded</strong>
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
        )}
      </DataState>
    </>
  );
}
