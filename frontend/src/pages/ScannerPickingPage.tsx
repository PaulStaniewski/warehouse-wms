import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { useOrderLines, useRouteRun } from "../api/queries";
import { DataState } from "../components/DataState";


function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(value));
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

function toNumber(value: string) {
  return Number.parseFloat(value);
}

function formatQuantity(value: number) {
  return new Intl.NumberFormat("en-GB", {
    maximumFractionDigits: 3,
  }).format(value);
}

export function ScannerPickingPage() {
  const { id } = useParams();
  const routeRun = useRouteRun(id);
  const orderLines = useOrderLines(id);
  const lines = orderLines.data?.results ?? [];
  const totalOrdered = lines.reduce((sum, line) => sum + toNumber(line.quantity_ordered), 0);
  const totalPicked = lines.reduce((sum, line) => sum + toNumber(line.quantity_picked), 0);
  const totalRemaining = lines.reduce((sum, line) => sum + toNumber(line.remaining_quantity), 0);

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner/routes">
          <ArrowLeft size={17} />
          Back to Scanner Routes
        </Link>
        <Link to="/routes-monitor">Back to Route Monitor</Link>
      </div>

      <DataState
        isLoading={routeRun.isLoading || orderLines.isLoading}
        isError={routeRun.isError || orderLines.isError}
        error={routeRun.error || orderLines.error}
      >
        {routeRun.data && (
          <section className="scanner-header-panel">
            <div>
              <p>{routeRun.data.branch_code}</p>
              <h1>
                {routeRun.data.route_code} <span>{routeRun.data.route_name}</span>
              </h1>
            </div>
            <dl>
              <div>
                <dt>Run</dt>
                <dd>{routeRun.data.run_number}</dd>
              </div>
              <div>
                <dt>Service date</dt>
                <dd>{formatDate(routeRun.data.service_date)}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{formatStatus(routeRun.data.status)}</dd>
              </div>
              <div>
                <dt>Cutoff</dt>
                <dd>{formatTime(routeRun.data.order_cutoff_time)}</dd>
              </div>
              <div>
                <dt>Sync</dt>
                <dd>{formatTime(routeRun.data.sync_time)}</dd>
              </div>
              <div>
                <dt>Departure</dt>
                <dd>{formatTime(routeRun.data.departure_time)}</dd>
              </div>
            </dl>
          </section>
        )}

        <section className="scanner-progress-grid">
          <article>
            <span>Ordered</span>
            <strong>{formatQuantity(totalOrdered)}</strong>
          </article>
          <article>
            <span>Picked</span>
            <strong>{formatQuantity(totalPicked)}</strong>
          </article>
          <article>
            <span>Remaining</span>
            <strong>{formatQuantity(totalRemaining)}</strong>
          </article>
        </section>

        {lines.length === 0 ? (
          <div className="state-box">No picking lines found for this route run.</div>
        ) : (
          <section className="picking-list">
            {lines.map((line) => (
              <article className="picking-row" key={line.id}>
                <div className="picking-location">
                  <span>Location</span>
                  <strong>{line.source_location_code ?? "Not assigned"}</strong>
                  {line.source_location_name && <small>{line.source_location_name}</small>}
                </div>

                <div className="picking-product">
                  <span className="mono">{line.product_sku}</span>
                  <h2>{line.product_name}</h2>
                  <p>Order {line.order_reference}</p>
                </div>

                <div className="picking-quantities">
                  <div>
                    <span>Ordered</span>
                    <strong>{line.quantity_ordered}</strong>
                  </div>
                  <div>
                    <span>Picked</span>
                    <strong>{line.quantity_picked}</strong>
                  </div>
                  <div>
                    <span>Remaining</span>
                    <strong>{line.remaining_quantity}</strong>
                  </div>
                </div>
              </article>
            ))}
          </section>
        )}
      </DataState>
    </>
  );
}
