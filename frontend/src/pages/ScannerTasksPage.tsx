import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Link, useNavigate } from "react-router-dom";

import { useScannerCartWork, useScannerJobs, useScannerTaskStart } from "../api/queries";
import { storeScannerSession, useStoredScannerSession } from "../api/scannerSession";
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
  const activeSession = useStoredScannerSession();
  const jobs = useScannerJobs();
  const cartWork = useScannerCartWork(activeSession?.id, activeSession?.cart_work_session);
  const startJob = useScannerTaskStart();
  const [selectedJob, setSelectedJob] = useState<PickingJob | null>(null);
  const [cartCode, setCartCode] = useState("");
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const rows = jobs.data?.results ?? [];
  const activeCartWork = cartWork.data?.cart_work_session;
  const activeJobId = activeCartWork?.picking_job.id;
  const myActiveJobs = rows.filter((job) => job.status === "in_progress" && job.id === activeJobId);
  const availableJobs = rows.filter((job) => job.status === "available");
  const otherActiveJobs = rows.filter((job) => job.status === "in_progress" && job.id !== activeJobId);

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedJob) {
      return;
    }

    setMessage(null);
    try {
      const result = await startJob.mutateAsync({ cartCode, jobId: selectedJob.id, workerCode });
      storeScannerSession({
        ...result.session,
        cart_work_session: result.cart_work_session.id,
        picking_job: result.job.id,
      });
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

  function handleResume() {
    if (activeCartWork?.scanner_session) {
      storeScannerSession({
        ...activeCartWork.scanner_session,
        cart_work_session: activeCartWork.id,
        picking_job: activeCartWork.picking_job.id,
      });
    }
    navigate("/scanner/picking");
  }

  function renderJobDetails(job: PickingJob) {
    return (
      <>
        <div>
          <span>Picking Job #{job.id}</span>
          <strong>{formatRoutes(job)}</strong>
          <small>{job.mode} / {job.status.replaceAll("_", " ")}</small>
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
      </>
    );
  }

  return (
    <>
      <PageHeader title="Tasks" description="Start available picking jobs or resume your active cart work." />

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <DataState isLoading={jobs.isLoading || cartWork.isLoading} isError={jobs.isError} error={jobs.error}>
        {rows.length === 0 ? (
          <div className="state-box">
            No available tasks. <Link to="/scanner/proformas">Create a picking job from Proformas.</Link>
          </div>
        ) : (
          <div className="scanner-task-sections">
            {myActiveJobs.length > 0 && (
              <section>
                <h2>My Active Task</h2>
                <div className="scanner-job-grid">
                  {myActiveJobs.map((job) => (
                    <article className="scanner-job-card scanner-job-card--active" key={job.id}>
                      {renderJobDetails(job)}
                      <button className="scanner-job-action" onClick={handleResume} type="button">
                        Resume Picking
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            )}

            <section>
              <h2>Available Tasks</h2>
              {availableJobs.length === 0 ? (
                <div className="state-box">No available picking jobs.</div>
              ) : (
                <div className="scanner-job-grid">
                  {availableJobs.map((job) => (
                    <article
                      className={`scanner-job-card ${selectedJob?.id === job.id ? "scanner-job-card--selected" : ""}`}
                      key={job.id}
                    >
                      {renderJobDetails(job)}
                      <button className="scanner-job-action" onClick={() => setSelectedJob(job)} type="button">
                        Start
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </section>

            {otherActiveJobs.length > 0 && (
              <section>
                <h2>Other In-Progress Tasks</h2>
                <div className="scanner-job-grid">
                  {otherActiveJobs.map((job) => (
                    <article className="scanner-job-card scanner-job-card--locked" key={job.id}>
                      {renderJobDetails(job)}
                      <span className="scanner-job-lock">In progress</span>
                    </article>
                  ))}
                </div>
              </section>
            )}
          </div>
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
