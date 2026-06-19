import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Lock, Route, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";

import { useBranches, useRouteRuns } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { Branch, RouteRun } from "../types/api";


function getDefaultBranch(branches: Branch[]) {
  return branches.find((branch) => branch.code === "GDY") ?? branches[0];
}

function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

function isTerminalStatus(status: string) {
  return ["closed", "dispatched", "cancelled"].includes(status);
}

function isLocallySelectable(run: RouteRun, hasPriorityMode: boolean) {
  if (!run.has_pending_work || isTerminalStatus(run.status)) {
    return false;
  }

  return hasPriorityMode ? run.is_urgent : true;
}

function getRunLabel(run: RouteRun, hasPriorityMode: boolean) {
  if (!run.has_pending_work) {
    return { label: "No pending work", tone: "neutral" };
  }

  if (run.is_urgent) {
    return { label: "Urgent", tone: "urgent" };
  }

  if (isLocallySelectable(run, hasPriorityMode)) {
    return { label: "Selectable", tone: "selectable" };
  }

  return { label: "Locked", tone: "locked" };
}

function ScannerRouteCard({ hasPriorityMode, run }: { hasPriorityMode: boolean; run: RouteRun }) {
  const label = getRunLabel(run, hasPriorityMode);
  const canOpen = isLocallySelectable(run, hasPriorityMode);
  const content = (
    <>
      <header className="scanner-card-header">
        <div>
          <p>{run.branch_code}</p>
          <h2>{run.route_code}</h2>
          <span>{run.route_name}</span>
        </div>
        <div className="route-run-icon">
          {run.is_urgent ? <AlertTriangle size={22} /> : canOpen ? <Route size={22} /> : <Lock size={22} />}
        </div>
      </header>

      <div className="scanner-run-meta">
        <strong>Run {run.run_number}</strong>
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

      <div className="scanner-pending">
        <span>Pending lines</span>
        <strong>{run.pending_lines_count}</strong>
      </div>

      <span className={`route-label route-label--${label.tone}`}>{label.label}</span>
    </>
  );

  if (!canOpen) {
    return <article className="scanner-route-card scanner-route-card--disabled">{content}</article>;
  }

  return (
    <Link className="scanner-route-card" to={`/scanner/route-runs/${run.id}/picking`}>
      {content}
    </Link>
  );
}

export function ScannerRoutesPage() {
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
        title="Scanner route selection"
        description="Choose a selectable route run before opening the read-only picking list."
        action={
          <div className="branch-selector">
            <label htmlFor="scanner-branch-select">Branch</label>
            <select
              disabled={branches.isLoading || branchRows.length === 0}
              id="scanner-branch-select"
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
          <div className="state-box">No route runs found for this branch.</div>
        ) : (
          <section className="scanner-routes-grid">
            {rows.map((run) => (
              <ScannerRouteCard hasPriorityMode={hasPriorityMode} key={run.id} run={run} />
            ))}
          </section>
        )}
      </DataState>
    </>
  );
}
