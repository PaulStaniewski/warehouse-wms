import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { useCompletePickingTask, usePickingTasks, useRouteRun } from "../api/queries";
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
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [scanValues, setScanValues] = useState<Record<number, { locationCode: string; productCode: string }>>({});
  const routeRun = useRouteRun(id);
  const pickingTasks = usePickingTasks(id);
  const completePickingTask = useCompletePickingTask();
  const tasks = pickingTasks.data?.results ?? [];
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);
  const openTasksCount = tasks.filter((task) => task.status === "open" || task.status === "assigned").length;
  const completedTasksCount = tasks.filter((task) => task.status === "completed").length;

  function updateScanValue(taskId: number, field: "locationCode" | "productCode", value: string) {
    setScanValues((current) => ({
      ...current,
      [taskId]: {
        locationCode: current[taskId]?.locationCode ?? "",
        productCode: current[taskId]?.productCode ?? "",
        [field]: value,
      },
    }));
  }

  async function handleComplete(taskId: number) {
    setMessage(null);
    const values = scanValues[taskId] ?? { locationCode: "", productCode: "" };

    try {
      const result = await completePickingTask.mutateAsync({
        locationCode: values.locationCode,
        productCode: values.productCode,
        taskId,
      });
      setMessage({ type: "success", text: result.message });
      setScanValues((current) => {
        const next = { ...current };
        delete next[taskId];
        return next;
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["picking-tasks", id] }),
        queryClient.invalidateQueries({ queryKey: ["route-run", id] }),
      ]);
    } catch (error) {
      const text = axios.isAxiosError(error)
        ? error.response?.data?.detail || "Could not complete picking task."
        : "Could not complete picking task.";
      setMessage({ type: "error", text });
    }
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner/routes">
          <ArrowLeft size={17} />
          Back to Scanner Routes
        </Link>
        <Link to="/wms/routes-monitor">Back to Route Monitor</Link>
      </div>

      <DataState
        isLoading={routeRun.isLoading || pickingTasks.isLoading}
        isError={routeRun.isError || pickingTasks.isError}
        error={routeRun.error || pickingTasks.error}
      >
        {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

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
              <article className={`picking-row ${task.status === "completed" ? "picking-row--completed" : ""}`} key={task.id}>
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

                <div className="picking-scan-fields">
                  <label>
                    <span>Scan location</span>
                    <input
                      disabled={task.status === "completed" || task.status === "cancelled"}
                      onChange={(event) => updateScanValue(task.id, "locationCode", event.target.value)}
                      placeholder={task.source_location_code}
                      value={scanValues[task.id]?.locationCode ?? ""}
                    />
                  </label>
                  <label>
                    <span>Scan product barcode or SKU</span>
                    <input
                      disabled={task.status === "completed" || task.status === "cancelled"}
                      onChange={(event) => updateScanValue(task.id, "productCode", event.target.value)}
                      placeholder={task.product_sku}
                      value={scanValues[task.id]?.productCode ?? ""}
                    />
                  </label>
                </div>

                <div className="picking-actions">
                  <button
                    disabled={
                      task.status === "completed" ||
                      task.status === "cancelled" ||
                      completePickingTask.isPending
                    }
                    onClick={() => handleComplete(task.id)}
                    type="button"
                  >
                    {task.status === "completed" ? "Completed" : "Complete"}
                  </button>
                </div>
              </article>
            ))}
          </section>
        )}
      </DataState>
    </>
  );
}
