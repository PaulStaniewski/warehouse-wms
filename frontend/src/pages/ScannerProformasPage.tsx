import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";

import { useBranches, useScannerCreateJobs, useScannerProformas } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { Branch } from "../types/api";


function getDefaultBranch(branches: Branch[]) {
  return branches.find((branch) => branch.code === "GDY") ?? branches[0];
}

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

export function ScannerProformasPage() {
  const queryClient = useQueryClient();
  const branches = useBranches();
  const branchRows = useMemo(() => branches.data?.results ?? [], [branches.data?.results]);
  const [selectedBranchId, setSelectedBranchId] = useState<number | undefined>();
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const proformas = useScannerProformas(selectedBranchId);
  const createJobs = useScannerCreateJobs();
  const rows = proformas.data?.results ?? [];

  useEffect(() => {
    if (selectedBranchId || branchRows.length === 0) {
      return;
    }
    setSelectedBranchId(getDefaultBranch(branchRows).id);
  }, [branchRows, selectedBranchId]);

  function toggleRouteRun(id: number) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  }

  async function handleCreate(mode: "merged" | "separate") {
    setMessage(null);
    try {
      const result = await createJobs.mutateAsync({ mode, routeRunIds: selectedIds, workerCode });
      setMessage({ type: "success", text: `${result.jobs.length} picking job(s) created.` });
      setSelectedIds([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-proformas"] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not create picking jobs.") });
    }
  }

  return (
    <>
      <PageHeader
        title="Proformas"
        description="Select routes and create picking jobs."
        action={
          <div className="branch-selector">
            <label htmlFor="scanner-proforma-branch">Branch</label>
            <select
              disabled={branches.isLoading || branchRows.length === 0}
              id="scanner-proforma-branch"
              onChange={(event) => {
                setSelectedBranchId(Number(event.target.value));
                setSelectedIds([]);
              }}
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

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <section className="scanner-action-strip">
        <label htmlFor="proforma-worker">
          <span>Worker</span>
          <input id="proforma-worker" onChange={(event) => setWorkerCode(event.target.value)} value={workerCode} />
        </label>
        <button disabled={selectedIds.length === 0 || createJobs.isPending} onClick={() => handleCreate("merged")} type="button">
          Merge
        </button>
        <button disabled={selectedIds.length === 0 || createJobs.isPending} onClick={() => handleCreate("separate")} type="button">
          Separate
        </button>
      </section>

      <DataState
        isLoading={branches.isLoading || proformas.isLoading || !selectedBranchId}
        isError={branches.isError || proformas.isError}
        error={branches.error || proformas.error}
      >
        {rows.length === 0 ? (
          <div className="state-box">No proformas found for the selected branch.</div>
        ) : (
          <section className="scanner-table-panel">
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th>Route</th>
                  <th>AKT</th>
                  <th>Lines</th>
                  <th>Started</th>
                  <th>Picked</th>
                  <th>Prepared</th>
                  <th>Departure</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => (
                  <tr className={!run.is_selectable ? "scanner-muted-row" : ""} key={run.id}>
                    <td>
                      <input
                        checked={selectedIds.includes(run.id)}
                        disabled={!run.is_selectable}
                        onChange={() => toggleRouteRun(run.id)}
                        type="checkbox"
                      />
                    </td>
                    <td>
                      <strong>{run.route_code}</strong>
                      <br />
                      <span>{run.branch_code} / run {run.run_number}</span>
                    </td>
                    <td>{run.akt}</td>
                    <td>{run.lines}</td>
                    <td>{run.started}</td>
                    <td>{run.picked}</td>
                    <td>{run.prepared}</td>
                    <td>{formatTime(run.departure_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </DataState>
    </>
  );
}
