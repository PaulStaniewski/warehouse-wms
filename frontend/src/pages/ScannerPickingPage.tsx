import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { usePickingTasks, useRouteRun, useScannerPickingPick } from "../api/queries";
import { useStoredScannerSession } from "../api/scannerSession";
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
  const [pickCode, setPickCode] = useState("");
  const [pickQuantity, setPickQuantity] = useState("1");
  const activeSession = useStoredScannerSession();
  const routeRun = useRouteRun(id);
  const pickingTasks = usePickingTasks(id);
  const scannerPickingPick = useScannerPickingPick();
  const tasks = pickingTasks.data?.results ?? [];
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalPrepared = tasks.reduce((sum, task) => sum + toNumber(task.quantity_prepared), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);
  const openTasksCount = tasks.filter((task) => task.status === "open" || task.status === "assigned").length;
  const pickedTasksCount = tasks.filter((task) => task.status === "picked").length;
  const completedTasksCount = tasks.filter((task) => task.status === "completed").length;

  async function refreshPickingData() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["picking-tasks", id] }),
      queryClient.invalidateQueries({ queryKey: ["route-run", id] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", activeSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }

  async function handlePick() {
    setMessage(null);
    const routeRunId = Number(id);

    try {
      const result = await scannerPickingPick.mutateAsync({
        code: pickCode,
        quantity: pickQuantity,
        routeRunId,
        sessionId: activeSession?.id ?? 0,
      });
      setMessage({ type: "success", text: result.message });
      setPickCode("");
      await refreshPickingData();
    } catch (error) {
      const text = axios.isAxiosError(error)
        ? error.response?.data?.detail || "Could not pick from shelf."
        : "Could not pick from shelf.";
      setMessage({ type: "error", text });
    }
  }

  function handlePickSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    handlePick();
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner/picking">
          <ArrowLeft size={17} />
          Pobranie
        </Link>
        <Link to="/scanner/control">Kontrola</Link>
        <Link to="/wms/routes-monitor">Route Monitor</Link>
      </div>

      <DataState
        isLoading={routeRun.isLoading || pickingTasks.isLoading}
        isError={routeRun.isError || pickingTasks.isError}
        error={routeRun.error || pickingTasks.error}
      >
        {!activeSession && (
          <div className="scanner-message scanner-message--error">
            Brak aktywnego wózka. <Link to="/scanner">Zeskanuj wózek</Link> przed pobraniem.
          </div>
        )}

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
            <span>Prepared</span>
            <strong>{formatQuantity(totalPrepared)}</strong>
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
          <article>
            <span>Picked tasks</span>
            <strong>{pickedTasksCount}</strong>
          </article>
        </section>

        <form className="scanner-workflow-panel" onSubmit={handlePickSubmit}>
          <header>
            <span>A</span>
            <h2>Pobranie</h2>
          </header>
          <label htmlFor="pick-code">
            <span>Zeskanuj produkt</span>
            <input
              autoComplete="off"
              autoFocus
              id="pick-code"
              onChange={(event) => setPickCode(event.target.value)}
              placeholder="SKU, kod kreskowy lub numer zamówienia"
              value={pickCode}
            />
          </label>
          <label htmlFor="pick-quantity">
            <span>Ilość</span>
            <input
              id="pick-quantity"
              min="0.001"
              onChange={(event) => setPickQuantity(event.target.value)}
              step="0.001"
              type="number"
              value={pickQuantity}
            />
          </label>
          <button disabled={!activeSession || scannerPickingPick.isPending || !pickCode.trim() || !pickQuantity} type="submit">
            {scannerPickingPick.isPending ? "Pobieranie..." : "Zatwierdź pobranie"}
          </button>
        </form>

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
                    <span>Prepared</span>
                    <strong>{task.quantity_prepared}</strong>
                  </div>
                  <div>
                    <span>To prepare</span>
                    <strong>{task.remaining_to_prepare}</strong>
                  </div>
                </div>

                <span className={`route-label route-label--${task.status === "completed" ? "selectable" : "neutral"}`}>
                  {formatStatus(task.status)}
                </span>
              </article>
            ))}
          </section>
        )}
      </DataState>
    </>
  );
}
