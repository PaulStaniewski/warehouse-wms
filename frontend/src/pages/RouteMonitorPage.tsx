import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarClock, Clock3, RefreshCw } from "lucide-react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useInterBranchMMTasks, usePrintRouteDocuments, useRouteRuns } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { InterBranchMMTask, RouteRun } from "../types/api";


function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatTime(value: string | null | undefined) {
  if (!value) return "-";
  if (value.includes("T")) {
    return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }
  return value.slice(0, 5);
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  });
}

function formatUnits(value: number) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(value);
}

function getMmProgress(task: InterBranchMMTask) {
  if (task.expected_units <= 0) {
    return 0;
  }

  return Math.min(100, Math.round((task.put_away_units / task.expected_units) * 100));
}

function getMmStatusLabel(task: InterBranchMMTask) {
  return task.status === "receiving" || task.put_away_units > 0 ? "Receiving" : "Waiting for receiving";
}

function needsAttention(run: RouteRun) {
  return ["cutoff_warning", "ready", "delayed"].includes(run.attention_status);
}

function getRowTone(run: RouteRun) {
  if (run.attention_status === "delayed") return "delayed";
  if (run.attention_status === "cutoff_warning") return "attention";
  if (run.attention_status === "ready") return "complete";
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

function MMTasksSection({ tasks }: { tasks: InterBranchMMTask[] }) {
  return (
    <section className="monitor-mm-section">
      <header className="monitor-section-header">
        <div>
          <h2>MM / Inter-branch transfers</h2>
          <p>Pallets waiting for put-away at the active branch.</p>
        </div>
        <strong>
          {tasks.length} active {tasks.length === 1 ? "task" : "tasks"}
        </strong>
      </header>

      {tasks.length === 0 ? (
        <div className="monitor-empty-state">No pallets are waiting for receiving.</div>
      ) : (
        <div className="monitor-mm-list">
          {tasks.map((task) => {
            const progress = getMmProgress(task);

            return (
              <article className="monitor-mm-card" key={task.pallet_id}>
                <div className="monitor-mm-primary">
                  <div>
                    <span>Pallet</span>
                    <strong>{task.pallet_code}</strong>
                  </div>
                  <span className={`monitor-mm-status monitor-mm-status--${task.status}`}>
                    {getMmStatusLabel(task)}
                  </span>
                </div>

                <div className="monitor-mm-route">
                  <div>
                    <span>Transfer</span>
                    <strong>{task.transfer_reference}</strong>
                  </div>
                  <div>
                    <span>From</span>
                    <strong>{task.source_branch}</strong>
                  </div>
                  <div>
                    <span>To</span>
                    <strong>{task.destination_branch}</strong>
                  </div>
                  <div>
                    <span>Arrived</span>
                    <strong>{formatDateTime(task.arrived_at)}</strong>
                  </div>
                </div>

                <div className="monitor-mm-quantities">
                  <div>
                    <span>Expected units</span>
                    <strong>{formatUnits(task.expected_units)}</strong>
                  </div>
                  <div>
                    <span>Put away units</span>
                    <strong>{formatUnits(task.put_away_units)}</strong>
                  </div>
                  <div className="monitor-mm-remaining">
                    <span>Remaining units</span>
                    <strong>{formatUnits(task.remaining_units)}</strong>
                  </div>
                </div>

                <div className="monitor-mm-footer">
                  <div className="monitor-mm-progress">
                    <div>
                      <strong>
                        {formatUnits(task.put_away_units)} / {formatUnits(task.expected_units)} put away
                      </strong>
                      <span>{progress}%</span>
                    </div>
                    <div className="monitor-progress-track">
                      <div className="monitor-progress-fill" style={{ width: `${progress}%` }} />
                    </div>
                  </div>

                  <Link
                    className="monitor-mm-action"
                    to={`/scanner/receiving?pallet=${encodeURIComponent(task.pallet_code)}`}
                  >
                    Open receiving
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function RouteList({
  onSelect,
  rows,
  selectedRouteRunId,
}: {
  onSelect: (run: RouteRun) => void;
  rows: RouteRun[];
  selectedRouteRunId?: number;
}) {
  return (
    <div className="monitor-route-list">
      <div className="monitor-route-head">
        <span>Route</span>
        <span>Active</span>
        <span>Lines</span>
        <span>Started</span>
        <span>Picked</span>
        <span>Prepared</span>
        <span>Cutoff</span>
        <span>Departure</span>
      </div>

      {rows.map((run) => {
        const tone = getRowTone(run);

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
              <strong>{run.operational_identifier || run.route_code}</strong>
              <small>{formatStatus(run.status)} / {run.attention_reason}</small>
            </div>
            <div className="monitor-count monitor-count--active">{run.active_workers_count}</div>
            <div className="monitor-count" title={`${run.total_active_lines} total active lines`}>
              {run.unstarted_lines_count}
            </div>
            <div className="monitor-count">{run.started_lines_count}</div>
            <div className="monitor-count">{run.picked_line_bucket_count}</div>
            <div className="monitor-count">{run.prepared_line_bucket_count}</div>
            <div className="monitor-departure">{formatTime(run.cutoff_at || run.order_cutoff_time)}</div>
            <div className="monitor-departure">{formatTime(run.planned_departure_at || run.departure_time)}</div>
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
  const rows = routeRuns.data?.results ?? [];
  const delayedRows = rows.filter((run) => run.attention_status === "delayed");
  const warningRows = rows.filter((run) => run.attention_status === "cutoff_warning");
  const attentionRoutes = rows
    .filter((run) => needsAttention(run))
    .filter((run, index, list) => list.findIndex((item) => item.id === run.id) === index)
    .sort((left, right) => (left.planned_departure_at || left.departure_time).localeCompare(right.planned_departure_at || right.departure_time));

  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    setSelectedRouteRun(null);
    setActionMessage(null);
  }, [activeBranchCode]);

  useEffect(() => {
    if (!activeBranchCode) {
      return undefined;
    }

    const refresh = window.setInterval(() => {
      routeRuns.refetch();
      mmTasks.refetch();
    }, 10000);

    return () => window.clearInterval(refresh);
  }, [activeBranchCode]);

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
            <Link className="monitor-refresh-button" to="/wms/route-schedules">
              <CalendarClock size={16} />
              Schedule
            </Link>
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
            {(warningRows.length > 0 || delayedRows.length > 0) && (
              <div className="monitor-priority-banner">
                <AlertTriangle size={18} />
                <span>{delayedRows.length} delayed / {warningRows.length} cutoff warnings</span>
              </div>
            )}
          </header>

          <div className="monitor-layout">
            <section className="monitor-main-panel">
              {rows.length === 0 ? (
                <div className="state-box">No route runs found.</div>
              ) : (
                <RouteList
                  onSelect={setSelectedRouteRun}
                  rows={rows}
                  selectedRouteRunId={selectedRouteRun?.id}
                />
              )}

              <MMTasksSection tasks={mmTasks.data?.results ?? []} />

              <section className="monitor-side-section monitor-inventory-section">
                <h2>Inventory tasks</h2>
                <p>No inventory tasks</p>
              </section>
            </section>

            <aside className="monitor-side-panel">
              <section className="monitor-clock">
                <Clock3 size={26} />
                <span>{now.toLocaleTimeString("en-GB")}</span>
                <small>{now.toLocaleDateString("en-GB")}</small>
              </section>

              <section className="monitor-side-section">
                <h2>Routes requiring attention</h2>
                {attentionRoutes.length === 0 ? (
                  <p>No route runs require attention.</p>
                ) : (
                  <ul className="monitor-attention-list">
                    {attentionRoutes.map((run) => (
                      <li key={run.id}>
                        <div>
                          <strong>{run.operational_identifier}</strong>
                          <span>{formatTime(run.planned_departure_at || run.departure_time)}</span>
                        </div>
                        <small>
                          Active {run.active_workers_count} / {run.total_active_lines} lines
                        </small>
                        <ProgressCell run={run} />
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>Route documents</h2>
                {actionMessage && <p className={`monitor-action-message monitor-action-message--${actionMessage.type}`}>{actionMessage.text}</p>}
                {!selectedRouteRun ? (
                  <p>Select a route to view its read-only state and document action.</p>
                ) : (
                  <>
                    <p>
                      {selectedRouteRun.operational_identifier}
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
                    </div>
                  </>
                )}
              </section>
            </aside>
          </div>
        </section>
      </DataState>
    </>
  );
}
