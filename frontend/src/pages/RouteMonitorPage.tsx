import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Clock3, RefreshCw } from "lucide-react";

import { useBranches, useCurrentAuditLogs, useRouteRuns } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { AuditLog, Branch, RouteRun } from "../types/api";


function formatStatus(status: string) {
  return status.replaceAll("_", " ");
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

function needsAttention(run: RouteRun, now: Date) {
  if (isDelayed(run, now)) {
    return false;
  }

  return run.is_urgent || (run.has_pending_work && ["syncing", "picking", "ready_to_close"].includes(run.status));
}

function getRowTone(run: RouteRun, now: Date) {
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

function getLatestActivity(auditLog?: AuditLog) {
  if (!auditLog) {
    return "No scanner activity yet.";
  }

  const time = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(auditLog.created_at));

  return `${time} - ${auditLog.message}`;
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

function RouteList({ now, rows }: { now: Date; rows: RouteRun[] }) {
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
          <article className={`monitor-route-row monitor-route-row--${tone}`} key={run.id}>
            <div className="monitor-route-name">
              <strong>{run.route_code}</strong>
              <span>{run.route_name}</span>
              <small>{formatStatus(run.status)}</small>
            </div>
            <div className="monitor-count monitor-count--active">{run.open_picking_tasks}</div>
            <div className="monitor-count">{run.order_lines_count}</div>
            <div className="monitor-count">{run.in_progress_picking_tasks}</div>
            <div className="monitor-count">{run.picked_lines_count}</div>
            <div className="monitor-count">{run.completed_picking_tasks}</div>
            <ProgressCell run={run} />
            <div className="monitor-departure">{formatTime(run.departure_time)}</div>
          </article>
        );
      })}
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
  const rows = routeRuns.data?.results ?? [];
  const delayedRows = rows.filter((run) => isDelayed(run, now));
  const attentionRows = rows.filter((run) => needsAttention(run, now));
  const lowProgressRows = rows
    .filter((run) => run.has_pending_work && run.progress_percent < 50)
    .sort((left, right) => left.progress_percent - right.progress_percent)
    .slice(0, 4);
  const pendingPickingWork = rows.reduce(
    (total, run) => total + run.open_picking_tasks + run.in_progress_picking_tasks,
    0,
  );
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
                <RouteList now={now} rows={rows} />
              )}
            </section>

            <aside className="monitor-side-panel">
              <section className="monitor-clock">
                <Clock3 size={26} />
                <span>{now.toLocaleTimeString("en-GB")}</span>
                <small>{now.toLocaleDateString("en-GB")}</small>
              </section>

              <section className="monitor-side-section monitor-side-section--primary">
                <h2>Employee tasks / Picking</h2>
                <p className="monitor-big-number">{pendingPickingWork}</p>
                <p>Pending picking work</p>
                <div className="monitor-activity-line">{getLatestActivity(latestAuditLog)}</div>
              </section>

              <section className="monitor-side-section">
                <h2>Low progress routes</h2>
                {lowProgressRows.length === 0 ? (
                  <p>No low-progress active routes.</p>
                ) : (
                  <ul className="monitor-alert-list">
                    {lowProgressRows.map((run) => (
                      <li key={run.id}>
                        <strong>{run.route_code}</strong>
                        <span>{run.progress_percent}% / departure {formatTime(run.departure_time)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="monitor-side-section">
                <h2>MM / Inter-branch transfers</h2>
                <p>No MM tasks</p>
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
