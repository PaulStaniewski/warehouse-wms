import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Lock, Route, ShieldCheck } from "lucide-react";

import { useBranches, useRouteRuns } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { Branch, RouteRun } from "../types/api";


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

function getRunLabels(run: RouteRun) {
  if (!run.has_pending_work) {
    return [{ label: "No pending work", tone: "neutral" }];
  }

  if (run.is_urgent) {
    return [{ label: "Urgent", tone: "urgent" }];
  }

  if (run.is_selectable) {
    return [{ label: "Selectable", tone: "selectable" }];
  }

  return [{ label: "Locked", tone: "locked" }];
}

function RouteRunCard({ run }: { run: RouteRun }) {
  const labels = getRunLabels(run);
  const isLocked = !run.is_selectable && run.has_pending_work;

  return (
    <article className={`route-run-card ${isLocked ? "route-run-card--locked" : ""}`}>
      <header className="route-run-card-header">
        <div>
          <p className="route-run-kicker">{run.branch_code}</p>
          <h2>
            {run.route_code} <span>{run.route_name}</span>
          </h2>
        </div>
        <div className="route-run-icon">
          {run.is_urgent ? <AlertTriangle size={20} /> : isLocked ? <Lock size={20} /> : <Route size={20} />}
        </div>
      </header>

      <div className="route-run-meta">
        <span>Run {run.run_number}</span>
        <span>{formatDate(run.service_date)}</span>
        <span>{formatStatus(run.status)}</span>
      </div>

      <dl className="route-run-times">
        <div>
          <dt>Cutoff</dt>
          <dd>{formatTime(run.order_cutoff_time)}</dd>
        </div>
        <div>
          <dt>Sync</dt>
          <dd>{formatTime(run.sync_time)}</dd>
        </div>
        <div>
          <dt>Departure</dt>
          <dd>{formatTime(run.departure_time)}</dd>
        </div>
      </dl>

      <div className="route-run-counts">
        <div>
          <span>Orders</span>
          <strong>{run.orders_count}</strong>
        </div>
        <div>
          <span>Lines</span>
          <strong>{run.order_lines_count}</strong>
        </div>
        <div>
          <span>Pending</span>
          <strong>{run.pending_lines_count}</strong>
        </div>
      </div>

      <footer className="route-run-labels">
        {labels.map((item) => (
          <span className={`route-label route-label--${item.tone}`} key={item.label}>
            {item.label}
          </span>
        ))}
      </footer>
    </article>
  );
}

function getDefaultBranch(branches: Branch[]) {
  return branches.find((branch) => branch.code === "GDY") ?? branches[0];
}

export function RouteMonitorPage() {
  const branches = useBranches();
  const [selectedBranchId, setSelectedBranchId] = useState<number | undefined>();
  const branchRows = useMemo(() => branches.data?.results ?? [], [branches.data?.results]);
  const selectedBranch = branchRows.find((branch) => branch.id === selectedBranchId);
  const routeRuns = useRouteRuns(selectedBranchId);
  const rows = routeRuns.data?.results ?? [];
  const hasPriorityMode = rows.some((run) => run.is_urgent);

  useEffect(() => {
    if (selectedBranchId || branchRows.length === 0) {
      return;
    }

    setSelectedBranchId(getDefaultBranch(branchRows).id);
  }, [branchRows, selectedBranchId]);

  return (
    <>
      <PageHeader
        title="Route monitor"
        description="Read-only overview of route runs, departure windows, and picking pressure."
        action={
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
        }
      />

      <DataState
        isLoading={branches.isLoading || routeRuns.isLoading || !selectedBranchId}
        isError={branches.isError || routeRuns.isError}
        error={branches.error || routeRuns.error}
      >
        {selectedBranch && <p className="branch-context">Viewing branch: {selectedBranch.code}</p>}

        {hasPriorityMode && (
          <div className="priority-banner">
            <ShieldCheck size={18} />
            <span>Priority mode active - only urgent route runs can be selected.</span>
          </div>
        )}

        {rows.length === 0 ? (
          <div className="state-box">No route runs found.</div>
        ) : (
          <section className="route-monitor-grid">
            {rows.map((run) => (
              <RouteRunCard key={run.id} run={run} />
            ))}
          </section>
        )}
      </DataState>
    </>
  );
}
