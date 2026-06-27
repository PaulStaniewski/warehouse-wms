import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Clock3, RefreshCw, ShieldCheck } from "lucide-react";

import {
  useBranches,
  useCurrentAuditLogs,
  useInventoryItems,
  useReturnBatches,
  useRouteRuns,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { AuditLog, Branch, RouteRun } from "../types/api";


function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatDateTime(value: Date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
  }).format(value);
}

function formatActivity(value: string | null) {
  if (!value) {
    return "No activity";
  }

  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

function getDefaultBranch(branches: Branch[]) {
  return branches.find((branch) => branch.code === "GDY") ?? branches[0];
}

function isClosed(run: RouteRun) {
  return ["closed", "dispatched", "cancelled"].includes(run.status);
}

function isDelayed(run: RouteRun, now: Date) {
  if (isClosed(run) || !run.has_pending_work) {
    return false;
  }

  const departureAt = new Date(`${run.service_date}T${run.departure_time}`);
  return departureAt.getTime() < now.getTime();
}

function getStatusTone(run: RouteRun, now: Date) {
  if (isDelayed(run, now)) {
    return "delayed";
  }

  if (run.is_urgent) {
    return "urgent";
  }

  if (!run.is_selectable && run.has_pending_work) {
    return "locked";
  }

  if (["closed", "dispatched"].includes(run.status)) {
    return "completed";
  }

  if (["picking", "syncing", "ready_to_close"].includes(run.status)) {
    return "in-progress";
  }

  return "open";
}

function getLastAuditLabel(auditLog?: AuditLog) {
  if (!auditLog) {
    return "No audit activity in the current register.";
  }

  return `${formatActivity(auditLog.created_at)} - ${auditLog.message}`;
}

function ProgressCell({ run }: { run: RouteRun }) {
  const progress = Math.min(Math.max(run.progress_percent, 0), 100);

  return (
    <div className="monitor-progress-cell">
      <span>{progress}%</span>
      <div className="monitor-progress-track">
        <div className="monitor-progress-fill" style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

function RouteRunTable({ now, rows }: { now: Date; rows: RouteRun[] }) {
  return (
    <div className="monitor-table-wrap">
      <table className="monitor-table">
        <thead>
          <tr>
            <th>Route</th>
            <th>Run</th>
            <th>Branch</th>
            <th>Status</th>
            <th>Cutoff</th>
            <th>Sync</th>
            <th>Departure</th>
            <th>Orders</th>
            <th>Lines</th>
            <th>Open</th>
            <th>In progress</th>
            <th>Done</th>
            <th>Progress</th>
            <th>Last activity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => {
            const tone = getStatusTone(run, now);

            return (
              <tr className={`monitor-row monitor-row--${tone}`} key={run.id}>
                <td>
                  <strong>{run.route_code}</strong>
                  <span>{run.route_name}</span>
                </td>
                <td>#{run.run_number}</td>
                <td>{run.branch_code}</td>
                <td>
                  <span className={`monitor-status monitor-status--${tone}`}>{formatStatus(run.status)}</span>
                </td>
                <td>{formatTime(run.order_cutoff_time)}</td>
                <td>{formatTime(run.sync_time)}</td>
                <td>{formatTime(run.departure_time)}</td>
                <td>{run.orders_count}</td>
                <td>{run.order_lines_count}</td>
                <td>{run.open_picking_tasks}</td>
                <td>{run.in_progress_picking_tasks}</td>
                <td>{run.completed_picking_tasks}</td>
                <td>
                  <ProgressCell run={run} />
                </td>
                <td className="monitor-last-activity">{formatActivity(run.last_activity_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function RouteMonitorPage() {
  const branches = useBranches();
  const [selectedBranchId, setSelectedBranchId] = useState<number | undefined>();
  const [now, setNow] = useState(() => new Date());
  const branchRows = useMemo(() => branches.data?.results ?? [], [branches.data?.results]);
  const selectedBranch = branchRows.find((branch) => branch.id === selectedBranchId);
  const routeRuns = useRouteRuns(selectedBranchId);
  const auditLogs = useCurrentAuditLogs();
  const inventoryItems = useInventoryItems();
  const returnBatches = useReturnBatches();
  const rows = routeRuns.data?.results ?? [];
  const branchReturns = (returnBatches.data?.results ?? []).filter(
    (item) => item.branch === selectedBranchId && item.status === "verified",
  );
  const branchInventory = (inventoryItems.data?.results ?? []).filter((item) => item.branch === selectedBranchId);
  const urgentRows = rows.filter((run) => run.is_urgent);
  const delayedRows = rows.filter((run) => isDelayed(run, now));
  const hasPriorityMode = urgentRows.length > 0;
  const pendingTasks = rows.reduce((total, run) => total + run.open_picking_tasks + run.in_progress_picking_tasks, 0);
  const completedTasks = rows.reduce((total, run) => total + run.completed_picking_tasks, 0);
  const totalTasks = rows.reduce((total, run) => total + run.total_picking_tasks, 0);
  const latestAuditLog = auditLogs.data?.results[0];

  useEffect(() => {
    if (selectedBranchId || branchRows.length === 0) {
      return;
    }

    setSelectedBranchId(getDefaultBranch(branchRows).id);
  }, [branchRows, selectedBranchId]);

  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    const refresh = window.setInterval(() => {
      routeRuns.refetch();
      auditLogs.refetch();
    }, 10000);

    return () => window.clearInterval(refresh);
  }, [auditLogs, routeRuns]);

  function refreshMonitor() {
    routeRuns.refetch();
    auditLogs.refetch();
    inventoryItems.refetch();
    returnBatches.refetch();
  }

  return (
    <>
      <PageHeader
        title="Route Monitor"
        description="Read-only dispatch board for route progress, picking pressure, and scanner activity."
        action={
          <div className="monitor-header-actions">
            <button className="monitor-refresh-button" onClick={refreshMonitor} type="button">
              <RefreshCw size={16} />
              Refresh
            </button>
            <div className="branch-selector">
              <label htmlFor="branch-select">Branch</label>
              <select
                disabled={branches.isLoading || branchRows.length === 0}
                id="branch-select"
                onChange={(event) => setSelectedBranchId(Number(event.target.value))}
                value={selectedBranchId ?? ""}
              >
                {branchRows.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.code} / {branch.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        }
      />

      <DataState
        isLoading={branches.isLoading || routeRuns.isLoading || !selectedBranchId}
        isError={branches.isError || routeRuns.isError}
        error={branches.error || routeRuns.error}
      >
        <section className="monitor-board">
          <header className="monitor-board-header">
            <div>
              <p>Viewing branch: {selectedBranch?.code ?? "..."}</p>
              <strong>{selectedBranch?.name ?? "No branch selected"}</strong>
            </div>
            {hasPriorityMode && (
              <div className="monitor-priority-banner">
                <ShieldCheck size={18} />
                <span>Priority mode active - urgent route runs need attention first.</span>
              </div>
            )}
          </header>

          <div className="monitor-layout">
            <section className="monitor-main-panel">
              {rows.length === 0 ? (
                <div className="state-box">No route runs found.</div>
              ) : (
                <RouteRunTable now={now} rows={rows} />
              )}
            </section>

            <aside className="monitor-side-panel">
              <div className="monitor-clock">
                <Clock3 size={24} />
                <span>{now.toLocaleTimeString("en-GB")}</span>
                <small>{formatDateTime(now)}</small>
              </div>

              <div className="monitor-summary-grid">
                <div>
                  <span>Pending picking</span>
                  <strong>{pendingTasks}</strong>
                </div>
                <div>
                  <span>Completed tasks</span>
                  <strong>{completedTasks}</strong>
                </div>
                <div>
                  <span>Total tasks</span>
                  <strong>{totalTasks}</strong>
                </div>
                <div>
                  <span>Verified returns</span>
                  <strong>{branchReturns.length}</strong>
                </div>
              </div>

              <section className="monitor-side-section">
                <h2>
                  <AlertTriangle size={16} />
                  Urgent route runs
                </h2>
                {urgentRows.length === 0 ? (
                  <p>No urgent route runs.</p>
                ) : (
                  <ul className="monitor-alert-list">
                    {urgentRows.map((run) => (
                      <li key={run.id}>
                        <strong>{run.route_code}</strong>
                        <span>Departure {formatTime(run.departure_time)} / {run.open_picking_tasks} open</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>Delayed runs</h2>
                {delayedRows.length === 0 ? (
                  <p>No delayed pending route runs.</p>
                ) : (
                  <ul className="monitor-alert-list">
                    {delayedRows.map((run) => (
                      <li key={run.id}>
                        <strong>{run.route_code}</strong>
                        <span>Departure passed at {formatTime(run.departure_time)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>Last activity</h2>
                <p>{getLastAuditLabel(latestAuditLog)}</p>
              </section>

              <section className="monitor-side-section">
                <h2>Inventory / returns</h2>
                <p>{branchInventory.length} inventory positions visible for this branch.</p>
                <p>{branchReturns.length} verified return batches waiting for put-away.</p>
              </section>
            </aside>
          </div>
        </section>
      </DataState>
    </>
  );
}
