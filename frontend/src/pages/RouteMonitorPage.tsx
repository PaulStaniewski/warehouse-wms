import { useEffect, useState } from "react";
import { AlertTriangle, Clock3, RefreshCw } from "lucide-react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCloseRouteRun, useInterBranchMMTasks, usePrintRouteDocuments, useRouteRuns } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { RouteRun } from "../types/api";


function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

function isClosed(run: RouteRun) {
  return ["closed", "dispatched", "cancelled"].includes(run.status);
}

function isDelayed(run: RouteRun, now: Date) {
  if (isClosed(run) || run.is_ready_to_close || !run.has_pending_work) {
    return false;
  }

  const departureAt = new Date(`${run.service_date}T${run.departure_time}`);
  return departureAt.getTime() < now.getTime();
}

function needsAttention(run: RouteRun, now: Date) {
  if (isDelayed(run, now)) {
    return false;
  }

  return run.is_urgent || (run.has_pending_work && ["syncing", "picking", "ready_to_close"].includes(run.status));
}

function getRowTone(run: RouteRun, now: Date) {
  if (run.is_ready_to_close) {
    return run.is_late || isDelayed(run, now) ? "delayed" : "complete";
  }

  if (isDelayed(run, now)) {
    return "delayed";
  }

  if (needsAttention(run, now)) {
    return "attention";
  }

  if (isClosed(run) || run.progress_percent >= 100) {
    return "complete";
  }

  return "normal";
}

function ProgressCell({ run }: { run: RouteRun }) {
  const progress = Math.min(Math.max(run.progress_percent, 0), 100);

  return (
    <div className="monitor-progress-cell">
      <strong>{progress}%</strong>
      <div className="monitor-progress-track">
        <div className="monitor-progress-fill" style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

function RouteList({
  now,
  onSelect,
  rows,
  selectedRouteRunId,
}: {
  now: Date;
  onSelect: (run: RouteRun) => void;
  rows: RouteRun[];
  selectedRouteRunId?: number;
}) {
  return (
    <div className="monitor-route-list">
      <div className="monitor-route-head">
        <span>Route</span>
        <span>AKT</span>
        <span>Lines</span>
        <span>Started</span>
        <span>Picked</span>
        <span>Prepared</span>
        <span>Progress</span>
        <span>Departure</span>
      </div>

      {rows.map((run) => {
        const tone = getRowTone(run, now);

        return (
          <button
            className={`monitor-route-row monitor-route-row--${tone} ${
              selectedRouteRunId === run.id ? "monitor-route-row--selected" : ""
            }`}
            key={run.id}
            onClick={() => onSelect(run)}
            type="button"
          >
            <div className="monitor-route-name">
              <strong>{run.route_code}</strong>
              <span>{run.route_name}</span>
              <small>{formatStatus(run.status)}</small>
            </div>
            <div className="monitor-count monitor-count--active">{run.open_picking_tasks}</div>
            <div className="monitor-count">{run.order_lines_count}</div>
            <div className="monitor-count">{run.in_progress_picking_tasks}</div>
            <div className="monitor-count">{run.picked_picking_tasks}</div>
            <div className="monitor-count">{run.completed_picking_tasks}</div>
            <ProgressCell run={run} />
            <div className="monitor-departure">{formatTime(run.departure_time)}</div>
          </button>
        );
      })}
    </div>
  );
}

export function RouteMonitorPage() {
  const { activeBranch, activeBranchCode, isLoading: isBranchLoading } = useActiveBranch();
  const [selectedRouteRun, setSelectedRouteRun] = useState<RouteRun | null>(null);
  const [actionMessage, setActionMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [now, setNow] = useState(() => new Date());
  const routeRuns = useRouteRuns(activeBranchCode);
  const mmTasks = useInterBranchMMTasks(activeBranchCode);
  const printDocuments = usePrintRouteDocuments();
  const closeRouteRun = useCloseRouteRun();
  const rows = routeRuns.data?.results ?? [];
  const delayedRows = rows.filter((run) => isDelayed(run, now));
  const attentionRows = rows.filter((run) => needsAttention(run, now));
  const routesToClose = [...delayedRows, ...attentionRows]
    .filter((run, index, list) => list.findIndex((item) => item.id === run.id) === index)
    .sort((left, right) => left.departure_time.localeCompare(right.departure_time));

  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    setSelectedRouteRun(null);
    setActionMessage(null);
  }, [activeBranchCode]);

  useEffect(() => {
    const refresh = window.setInterval(() => {
      routeRuns.refetch();
      mmTasks.refetch();
    }, 10000);

    return () => window.clearInterval(refresh);
  }, [routeRuns]);

  function refreshMonitor() {
    routeRuns.refetch();
    mmTasks.refetch();
  }

  async function handlePrintDocuments() {
    if (!selectedRouteRun) {
      return;
    }

    try {
      const result = await printDocuments.mutateAsync({ routeRunId: selectedRouteRun.id });
      setSelectedRouteRun(result.route_run);
      setActionMessage({ type: "success", text: result.message });
      await routeRuns.refetch();
      window.open(`/wms/route-runs/${selectedRouteRun.id}/documents`, "_blank");
    } catch (error) {
      setActionMessage({ type: "error", text: "Could not print route documents." });
    }
  }

  async function handleCloseRoute() {
    if (!selectedRouteRun) {
      return;
    }

    try {
      const result = await closeRouteRun.mutateAsync({ routeRunId: selectedRouteRun.id });
      setActionMessage({ type: "success", text: result.message });
      setSelectedRouteRun(null);
      await routeRuns.refetch();
    } catch (error) {
      setActionMessage({ type: "error", text: "Could not close route." });
    }
  }

  return (
    <>
      <PageHeader
        title="Route Monitor"
        description="Read-only dispatch wall for route picking progress and departure pressure."
        action={
          <div className="monitor-header-actions">
            <button className="monitor-refresh-button" onClick={refreshMonitor} type="button">
              <RefreshCw size={16} />
              Refresh
            </button>
          </div>
        }
      />

      <DataState
        isLoading={isBranchLoading || routeRuns.isLoading || !activeBranchCode}
        isError={routeRuns.isError}
        error={routeRuns.error}
      >
        <section className="monitor-board">
          <header className="monitor-board-header">
            <div>
              <p>Viewing branch: {activeBranch?.code ?? "..."}</p>
              <strong>{activeBranch?.name ?? "No branch selected"}</strong>
            </div>
            {(attentionRows.length > 0 || delayedRows.length > 0) && (
              <div className="monitor-priority-banner">
                <AlertTriangle size={18} />
                <span>{delayedRows.length} delayed / {attentionRows.length} need attention</span>
              </div>
            )}
          </header>

          <div className="monitor-layout">
            <section className="monitor-main-panel">
              {rows.length === 0 ? (
                <div className="state-box">No route runs found.</div>
              ) : (
                <RouteList
                  now={now}
                  onSelect={setSelectedRouteRun}
                  rows={rows}
                  selectedRouteRunId={selectedRouteRun?.id}
                />
              )}
            </section>

            <aside className="monitor-side-panel">
              <section className="monitor-clock">
                <Clock3 size={26} />
                <span>{now.toLocaleTimeString("en-GB")}</span>
                <small>{now.toLocaleDateString("en-GB")}</small>
              </section>

              <section className="monitor-side-section">
                <h2>Routes requiring attention / To close</h2>
                {routesToClose.length === 0 ? (
                  <p>No route runs require attention.</p>
                ) : (
                  <ul className="monitor-attention-list">
                    {routesToClose.map((run) => (
                      <li key={run.id}>
                        <div>
                          <strong>{run.route_code}</strong>
                          <span>{formatTime(run.departure_time)}</span>
                        </div>
                        <small>
                          AKT {run.open_picking_tasks} / {run.order_lines_count} lines
                        </small>
                        <ProgressCell run={run} />
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>Route actions</h2>
                {actionMessage && <p className={`monitor-action-message monitor-action-message--${actionMessage.type}`}>{actionMessage.text}</p>}
                {!selectedRouteRun ? (
                  <p>Select a ready route.</p>
                ) : (
                  <>
                    <p>
                      {selectedRouteRun.route_code} / run {selectedRouteRun.run_number}
                    </p>
                    <p>{selectedRouteRun.is_ready_to_close ? "Ready to close" : "Route is not ready yet."}</p>
                    <div className="monitor-action-buttons">
                      <button
                        disabled={!selectedRouteRun.is_ready_to_close || printDocuments.isPending}
                        onClick={handlePrintDocuments}
                        type="button"
                      >
                        Print route documents
                      </button>
                      <button
                        disabled={
                          !selectedRouteRun.is_ready_to_close ||
                          !selectedRouteRun.documents_printed_at ||
                          closeRouteRun.isPending
                        }
                        onClick={handleCloseRoute}
                        type="button"
                      >
                        Close route
                      </button>
                    </div>
                  </>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>MM / Inter-branch transfers</h2>
                <p>MM tasks: {mmTasks.data?.results.length ?? 0}</p>
                {(mmTasks.data?.results.length ?? 0) === 0 ? <p>No MM tasks</p> : (
                  <div className="table-wrap"><table><thead><tr><th>Pallet</th><th>Transfer</th><th>From</th><th>Arrived</th><th>Expected</th><th>Put away</th><th>Remaining</th><th>Status</th><th>Open</th></tr></thead>
                    <tbody>{mmTasks.data?.results.map((task) => <tr key={task.pallet_id}><td>{task.pallet_code}</td><td>{task.transfer_reference}</td><td>{task.source_branch}</td><td>{new Date(task.arrived_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</td><td>{task.expected_units}</td><td>{task.put_away_units}</td><td>{task.remaining_units}</td><td>{formatStatus(task.status)}</td><td><a href={`/scanner/receiving?pallet=${encodeURIComponent(task.pallet_code)}`}>Open receiving</a></td></tr>)}</tbody>
                  </table></div>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>Inventory tasks</h2>
                <p>No inventory tasks</p>
              </section>
            </aside>
          </div>
        </section>
      </DataState>
    </>
  );
}
