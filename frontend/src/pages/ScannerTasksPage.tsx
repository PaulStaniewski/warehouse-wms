import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { Link, useNavigate } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useAuth } from "../api/AuthContext";
import { useScannerCartWork, useScannerCartWorkJoin, useScannerJobs, useScannerTaskStart } from "../api/queries";
import { clearStoredScannerCartWork, storeScannerSession, useStoredScannerSession } from "../api/scannerSession";
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
  const auth = useAuth();
  const { activeBranch, activeMembership } = useActiveBranch();
  const activeSession = useStoredScannerSession();
  const [selectedJob, setSelectedJob] = useState<PickingJob | null>(null);
  const [cartCode, setCartCode] = useState("");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const jobs = useScannerJobs();
  const cartWork = useScannerCartWork(activeSession?.id, activeSession?.cart_work_session, {
    onStaleSession: () => {
      clearStoredScannerCartWork();
      queryClient.removeQueries({ queryKey: ["scanner-cart-work"] });
      setMessage({
        type: "success",
        text: "Previous picking session is no longer available. Open Tasks to start new work.",
      });
    },
  });
  const startJob = useScannerTaskStart();
  const joinCartWork = useScannerCartWorkJoin();
  const rows = jobs.data?.results ?? [];
  const activeCartWork = activeSession?.cart_work_session ? cartWork.data?.cart_work_session : undefined;
  const activeJobId = activeCartWork?.picking_job.id;
  const myActiveJobs = rows.filter(
    (job) =>
      job.status === "in_progress" &&
      (job.id === activeJobId || Boolean(auth.username && job.active_workers.includes(auth.username))),
  );
  const availableJobs = rows.filter((job) => job.status === "available");
  const joinableActiveJobs = rows.filter(
    (job) => job.status === "in_progress" && !myActiveJobs.some((activeJob) => activeJob.id === job.id),
  );

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedJob) {
      return;
    }

    setMessage(null);
    try {
      const result = await startJob.mutateAsync({ cartCode, jobId: selectedJob.id });
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

  function handleResume(job?: PickingJob) {
    if (!activeCartWork && job?.assigned_cart_code) {
      void handleJoin(job);
      return;
    }
    if (activeCartWork?.scanner_session) {
      storeScannerSession({
        ...activeCartWork.scanner_session,
        cart_work_session: activeCartWork.id,
        picking_job: activeCartWork.picking_job.id,
      });
    }
    navigate("/scanner/picking");
  }

  async function handleJoin(job: PickingJob) {
    if (!job.assigned_cart_code) {
      setMessage({ type: "error", text: "This picking job has no active cart to join." });
      return;
    }

    setMessage(null);
    try {
      const result = await joinCartWork.mutateAsync({ cartBarcode: job.assigned_cart_code });
      if (!result.session) {
        throw new Error("Missing scanner session.");
      }
      storeScannerSession({
        ...result.session,
        cart_work_session: result.cart_work_session.id,
        picking_job: result.cart_work_session.picking_job.id,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-cart-work"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
      navigate("/scanner/picking");
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not join cart work.") });
    }
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
        <div>
          <span>Active workers</span>
          <strong>{job.active_workers_count}</strong>
          <small>{job.active_workers.length > 0 ? job.active_workers.join(", ") : "No active workers"}</small>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Tasks" description="Start available picking jobs or resume your active cart work." />

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <section className="scanner-context-strip">
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
      </section>

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
                      <button className="scanner-job-action" onClick={() => handleResume(job)} type="button">
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

            {joinableActiveJobs.length > 0 && (
              <section>
                <h2>Joinable In-Progress Work</h2>
                <div className="scanner-job-grid">
                  {joinableActiveJobs.map((job) => (
                    <article className="scanner-job-card scanner-job-card--active" key={job.id}>
                      {renderJobDetails(job)}
                      <button
                        className="scanner-job-action"
                        disabled={joinCartWork.isPending}
                        onClick={() => void handleJoin(job)}
                        type="button"
                      >
                        Join
                      </button>
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
          <button disabled={!cartCode.trim() || startJob.isPending} type="submit">
            Start
          </button>
        </form>
      )}
    </>
  );
}
