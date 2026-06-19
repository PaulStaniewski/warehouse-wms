import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { usePickingTasks, useRouteRun } from "../api/queries";
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
  const pickingTasks = usePickingTasks(id);
  const tasks = pickingTasks.data?.results ?? [];
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);
  const openTasksCount = tasks.filter((task) => task.status === "open" || task.status === "assigned").length;
  const completedTasksCount = tasks.filter((task) => task.status === "completed").length;

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
        isLoading={routeRun.isLoading || pickingTasks.isLoading}
        isError={routeRun.isError || pickingTasks.isError}
        error={routeRun.error || pickingTasks.error}
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
            <span>To pick</span>
            <strong>{formatQuantity(totalToPick)}</strong>
          </article>
          <article>
            <span>Picked</span>
            <strong>{formatQuantity(totalPicked)}</strong>
          </article>
          <article>
            <span>Remaining</span>
            <strong>{formatQuantity(totalRemaining)}</strong>
          </article>
          <article>
            <span>Open tasks</span>
            <strong>{openTasksCount}</strong>
          </article>
          <article>
            <span>Completed tasks</span>
            <strong>{completedTasksCount}</strong>
          </article>
        </section>

        {tasks.length === 0 ? (
          <div className="state-box">No picking tasks found for this route run.</div>
        ) : (
          <section className="picking-list">
            {tasks.map((task) => (
              <article className="picking-row" key={task.id}>
                <div className="picking-location">
                  <span>Location</span>
                  <strong>{task.source_location_code ?? "Not assigned"}</strong>
                  {task.source_location_name && <small>{task.source_location_name}</small>}
                </div>

                <div className="picking-product">
                  <span className="mono">{task.product_sku}</span>
                  <h2>{task.product_name}</h2>
                  <p>Order {task.order_reference}</p>
                  <p>Status {formatStatus(task.status)}</p>
                </div>

                <div className="picking-quantities">
                  <div>
                    <span>To pick</span>
                    <strong>{task.quantity_to_pick}</strong>
                  </div>
                  <div>
                    <span>Picked</span>
                    <strong>{task.quantity_picked}</strong>
                  </div>
                  <div>
                    <span>Remaining</span>
                    <strong>{task.remaining_quantity}</strong>
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
