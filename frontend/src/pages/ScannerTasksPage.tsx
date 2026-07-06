import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Link, useNavigate } from "react-router-dom";

import { useScannerJobs, useScannerTaskStart } from "../api/queries";
import { storeScannerSession } from "../api/scannerSession";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { PickingJob } from "../types/api";


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatRoutes(job: PickingJob) {
  return job.routes.map((route) => `${route.route_code}/${route.run_number}`).join(", ");
}

export function ScannerTasksPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const jobs = useScannerJobs();
  const startJob = useScannerTaskStart();
  const [selectedJob, setSelectedJob] = useState<PickingJob | null>(null);
  const [cartCode, setCartCode] = useState("");
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const rows = jobs.data?.results ?? [];

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedJob) {
      return;
    }

    setMessage(null);
    try {
      const result = await startJob.mutateAsync({ cartCode, jobId: selectedJob.id, workerCode });
      storeScannerSession(result.session);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-cart-work"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
      navigate("/scanner/picking");
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not start the picking job.") });
    }
  }

  return (
    <>
      <PageHeader title="Tasks" description="Choose an available picking job and scan a cart." />

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <DataState isLoading={jobs.isLoading} isError={jobs.isError} error={jobs.error}>
        {rows.length === 0 ? (
          <div className="state-box">
            No available tasks. <Link to="/scanner/proformas">Create a picking job from Proformas.</Link>
          </div>
        ) : (
          <section className="scanner-job-grid">
            {rows.map((job) => (
              <button
                className={`scanner-job-card ${selectedJob?.id === job.id ? "scanner-job-card--selected" : ""}`}
                key={job.id}
                onClick={() => setSelectedJob(job)}
                type="button"
              >
                <div>
                  <span>Picking Job #{job.id}</span>
                  <strong>{formatRoutes(job)}</strong>
                  <small>{job.mode} / {job.status}</small>
                </div>
                <div>
                  <span>Progress</span>
                  <strong>{job.progress_percent}%</strong>
                  <small>{job.remaining_lines} lines left</small>
                </div>
                <div>
                  <span>Cart</span>
                  <strong>{job.assigned_cart_code ?? "-"}</strong>
                </div>
              </button>
            ))}
          </section>
        )}
      </DataState>

      {selectedJob && selectedJob.status === "available" && (
        <form className="scanner-workflow-panel" onSubmit={handleStart}>
          <header>
            <span>1</span>
            <h2>Start Picking Job #{selectedJob.id}</h2>
          </header>
          <label htmlFor="task-cart-code">
            <span>Scan cart</span>
            <input
              autoComplete="off"
              autoFocus
              id="task-cart-code"
              onChange={(event) => setCartCode(event.target.value)}
              placeholder="WOZEK-01"
              value={cartCode}
            />
          </label>
          <label htmlFor="task-worker-code">
            <span>Worker</span>
            <input id="task-worker-code" onChange={(event) => setWorkerCode(event.target.value)} value={workerCode} />
          </label>
          <button disabled={!cartCode.trim() || startJob.isPending} type="submit">
            Start
          </button>
        </form>
      )}
    </>
  );
}
