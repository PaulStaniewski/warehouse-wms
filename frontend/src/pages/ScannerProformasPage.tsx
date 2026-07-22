import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useScannerCreateJobs, useScannerProformas } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { ScannerStatusMessage } from "../components/scanner/ScannerUi";

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatTime(value: string | null | undefined) {
  if (!value) return "-";
  if (value.includes("T")) {
    return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }
  return value.slice(0, 5);
}

export function ScannerProformasPage() {
  const queryClient = useQueryClient();
  const auth = useAuth();
  const { activeBranch, activeMembership, isLoading: branchLoading } = useActiveBranch();
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [pendingMode, setPendingMode] = useState<"merged" | "separate" | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const proformas = useScannerProformas(activeBranch?.id);
  const createJobs = useScannerCreateJobs();
  const rows = proformas.data?.results ?? [];
  const visibleSelectableIds = useMemo(() => new Set(rows.filter((row) => row.is_selectable).map((row) => row.id)), [rows]);
  const selectedRows = rows.filter((row) => selectedIds.includes(row.id) && row.is_selectable);
  const selectedLines = selectedRows.reduce((sum, row) => sum + row.total_active_lines, 0);
  const selectedUnits = selectedRows.reduce((sum, row) => sum + Number(row.remaining_pickable_quantity), 0);

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => visibleSelectableIds.has(id)));
  }, [visibleSelectableIds]);

  function toggleRouteRun(id: number) {
    if (!visibleSelectableIds.has(id)) {
      return;
    }
    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  }

  async function handleCreate(mode: "merged" | "separate") {
    if (selectedRows.length === 0) {
      return;
    }
    setPendingMode(mode);
    setMessage(null);
    try {
      const result = await createJobs.mutateAsync({ mode, routeRunIds: selectedRows.map((row) => row.id) });
      setMessage({
        type: "success",
        text: mode === "merged" ? "Picking job created. Open Tasks to start work." : `${result.jobs.length} separate picking jobs created. Open Tasks to start work.`,
      });
      setSelectedIds([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-proformas"] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["shipments"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not create picking jobs.") });
    } finally {
      setPendingMode(null);
    }
  }

  return (
    <>
      <PageHeader title="Proformas" description="Select routes and create a picking job." />

      {message && <ScannerStatusMessage type={message.type}>{message.text}</ScannerStatusMessage>}

      <section className="scanner-proforma-hero">
        <div>
          <h2>Route selection</h2>
          <p>Select one or more routes below.</p>
        </div>
        <div className="scanner-context-strip">
          <div>
            <span>Working branch</span>
            <strong>{activeBranch ? `${activeBranch.code} / ${activeBranch.name}` : "-"}</strong>
          </div>
          <div>
            <span>Logged in operator</span>
            <strong>{auth.username ?? "-"}</strong>
          </div>
          <div>
            <span>Role</span>
            <strong>{activeMembership?.role_label ?? "-"}</strong>
          </div>
        </div>
      </section>

      <section className="scanner-selection-board">
        <div>
          <span>Selected</span>
          <strong>{selectedRows.length} routes selected</strong>
          <small>{selectedLines} lines / {selectedUnits} units</small>
        </div>
        <div>
          <button disabled={selectedRows.length === 0 || createJobs.isPending} onClick={() => handleCreate("merged")} type="button">
            Merge selected
          </button>
          <small>Create one picking job containing all selected routes.</small>
        </div>
        <div>
          <button disabled={selectedRows.length === 0 || createJobs.isPending} onClick={() => handleCreate("separate")} type="button">
            Create separate jobs
          </button>
          <small>Create one picking job for each selected route.</small>
        </div>
      </section>

      {pendingMode && (
        <section className="scanner-confirm-summary">
          <strong>{pendingMode === "merged" ? "Creating merged picking job" : "Creating separate picking jobs"}</strong>
          <span>Routes: {selectedRows.map((row) => row.route_code).join(", ")}</span>
          <span>Lines: {selectedLines}</span>
          <span>Operator: {auth.username ?? "-"}</span>
          <span>Branch: {activeBranch?.code ?? "-"}</span>
        </section>
      )}

      <DataState
        isLoading={branchLoading || proformas.isLoading || !activeBranch}
        isError={proformas.isError}
        error={proformas.error}
      >
        {rows.length === 0 ? (
          <div className="state-box">No routes have remaining picking work for the working branch.</div>
        ) : (
          <section className="scanner-proforma-list">
            {rows.map((run) => {
              const selected = selectedIds.includes(run.id);
              return (
                <article
                  className={[
                    "scanner-proforma-card",
                    !run.is_selectable ? "scanner-proforma-card--disabled" : "",
                    selected ? "scanner-proforma-card--selected" : "",
                    "scanner-proforma-card--" + run.attention_status,
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  key={run.id}
                >
                  <label>
                    <input
                      checked={selected}
                      disabled={!run.is_selectable}
                      onChange={() => toggleRouteRun(run.id)}
                      type="checkbox"
                    />
                    <span>
                      <strong>{run.operational_identifier || run.route_code}</strong>
                      <small>{run.route_name} / round {run.run_number}</small>
                    </span>
                  </label>
                  <div className="scanner-proforma-card__metrics">
                    <div>
                      <span>Status</span>
                      <strong>{run.status.replaceAll("_", " ")}</strong>
                    </div>
                    <div>
                      <span>Readiness</span>
                      <strong>{run.readiness_state.replaceAll("_", " ")}</strong>
                    </div>
                    <div>
                      <span>Attention</span>
                      <strong>{run.attention_status.replaceAll("_", " ")}</strong>
                    </div>
                    <div>
                      <span>Active</span>
                      <strong>{run.akt}</strong>
                    </div>
                    <div>
                      <span>Lines</span>
                      <strong>{run.lines}</strong>
                    </div>
                    <div>
                      <span>Started</span>
                      <strong>{run.started}</strong>
                    </div>
                    <div>
                      <span>Picked</span>
                      <strong>{run.picked}</strong>
                    </div>
                    <div>
                      <span>Prepared</span>
                      <strong>{run.prepared}</strong>
                    </div>
                    <div>
                      <span>Progress</span>
                      <strong>{run.progress_percent}%</strong>
                    </div>
                    <div>
                      <span>Cutoff</span>
                      <strong>{formatTime(run.cutoff_at || run.order_cutoff_time)}</strong>
                    </div>
                    <div>
                      <span>Departure</span>
                      <strong>{formatTime(run.planned_departure_at || run.departure_time)}</strong>
                    </div>
                  </div>
                  {!run.is_selectable && <p className="scanner-proforma-card__blocking">{run.blocking_reason}</p>}
                </article>
              );
            })}
          </section>
        )}
      </DataState>

      <div className="scanner-secondary-links">
        <Link to="/scanner/tasks">Open Tasks</Link>
      </div>
    </>
  );
}
