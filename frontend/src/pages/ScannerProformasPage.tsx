import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useScannerCreateJobs, useScannerProformas } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatTime(value: string) {
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
  const selectedLines = selectedRows.reduce((sum, row) => sum + row.lines, 0);
  const selectedUnits = selectedRows.reduce((sum, row) => sum + row.akt, 0);

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

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

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
          <div className="state-box">No proformas found for the working branch.</div>
        ) : (
          <section className="scanner-table-panel">
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th>Route</th>
                  <th>Status</th>
                  <th>AKT</th>
                  <th>Lines</th>
                  <th>Started</th>
                  <th>Picked</th>
                  <th>Prepared</th>
                  <th>Departure</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => {
                  const selected = selectedIds.includes(run.id);
                  return (
                    <tr
                      className={`${!run.is_selectable ? "scanner-muted-row" : ""} ${selected ? "scanner-selected-row" : ""}`}
                      key={run.id}
                      onClick={() => toggleRouteRun(run.id)}
                    >
                      <td>
                        <input
                          checked={selected}
                          disabled={!run.is_selectable}
                          onChange={() => toggleRouteRun(run.id)}
                          onClick={(event) => event.stopPropagation()}
                          type="checkbox"
                        />
                      </td>
                      <td>
                        <strong>{run.route_code}</strong>
                        <br />
                        <span>{run.branch_code} / run {run.run_number}</span>
                      </td>
                      <td>{run.status.replaceAll("_", " ")}</td>
                      <td>{run.akt}</td>
                      <td>{run.lines}</td>
                      <td>{run.started}</td>
                      <td>{run.picked}</td>
                      <td>{run.prepared}</td>
                      <td>{formatTime(run.departure_time)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        )}
      </DataState>

      <div className="scanner-secondary-links">
        <Link to="/scanner/tasks">Open Tasks</Link>
      </div>
    </>
  );
}
