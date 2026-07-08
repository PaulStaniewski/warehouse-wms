import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  useAcknowledgeTransferDiscrepancyReconciliation,
  useCompleteManualTransferDiscrepancyReconciliation,
  useTransferDiscrepancyReconciliation,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { sourceVerificationStatusLabel } from "../types/display";

function formatQuantity(value: string | number) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function DiscrepancyReconciliationDetailPage() {
  const { id } = useParams();
  const reconciliation = useTransferDiscrepancyReconciliation(id);
  const acknowledge = useAcknowledgeTransferDiscrepancyReconciliation();
  const completeManual = useCompleteManualTransferDiscrepancyReconciliation();
  const [reviewMode, setReviewMode] = useState(false);
  const [manualOutcome, setManualOutcome] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [manualReviewMode, setManualReviewMode] = useState(false);
  const [manualOperationId, setManualOperationId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = reconciliation.data;

  async function handleAcknowledge() {
    if (!data) {
      return;
    }
    if (!reviewMode) {
      setReviewMode(true);
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await acknowledge.mutateAsync({ reconciliationId: data.id, workerCode: "DEMO" });
      setReviewMode(false);
      await reconciliation.refetch();
      setMessage("Reconciliation case acknowledged.");
    } catch {
      setError("Could not acknowledge reconciliation case.");
    }
  }

  async function handleCompleteManual() {
    if (!data) {
      return;
    }
    if (!manualReviewMode) {
      setManualOperationId((current) => current ?? crypto.randomUUID());
      setManualReviewMode(true);
      setMessage(null);
      setError(null);
      return;
    }
    if (!manualOperationId) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await completeManual.mutateAsync({
        clientOperationId: manualOperationId,
        decisionNote,
        outcome: manualOutcome,
        reconciliationId: data.id,
        workerCode: "DEMO",
      });
      setManualReviewMode(false);
      setManualOperationId(null);
      await reconciliation.refetch();
      setMessage("Reconciliation completed with a final manual outcome.");
    } catch {
      setError("Could not complete manual reconciliation.");
    }
  }

  const selectedOutcomeLabel =
    manualOutcome === "source_loss_confirmed"
      ? "Source loss confirmed"
      : manualOutcome === "unresolved_loss_closed"
        ? "Unresolved loss - cause not determined"
        : manualOutcome === "administrative_error"
          ? "Administrative or process error"
          : "";

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/wms/discrepancy-reconciliations">
          <ArrowLeft size={17} />
          Reconciliations
        </Link>
      </div>

      <PageHeader title={data?.reference ?? "Reconciliation"} description="Operational routing for a confirmed shortage." />

      {message && <div className="scanner-message scanner-message--success">{message}</div>}
      {error && <div className="scanner-message scanner-message--error">{error}</div>}

      <DataState isLoading={reconciliation.isLoading} isError={reconciliation.isError} error={reconciliation.error}>
        {data && (
          <>
            <section className="summary-grid">
              <article className="summary-card">
                <span>Status</span>
                <strong>{data.status_label}</strong>
              </article>
              <article className="summary-card">
                <span>Route</span>
                <strong>{data.route_label}</strong>
              </article>
              <article className="summary-card">
                <span>Discrepancy</span>
                <strong>{data.discrepancy_reference}</strong>
              </article>
              <article className="summary-card">
                <span>Source review</span>
                <strong>{data.source_review_reference}</strong>
              </article>
              <article className="summary-card">
                <span>Transfer</span>
                <strong>{data.transfer_reference}</strong>
              </article>
              <article className="summary-card">
                <span>Pallet</span>
                <strong>{data.pallet_code}</strong>
              </article>
              <article className="summary-card">
                <span>Branches</span>
                <strong>
                  {data.source_branch_code} to {data.destination_branch_code}
                </strong>
              </article>
              <article className="summary-card">
                <span>Confirmed shortage</span>
                <strong>{formatQuantity(data.total_confirmed_shortage_quantity)}</strong>
              </article>
              {data.manual_decision && (
                <article className="summary-card">
                  <span>Final outcome</span>
                  <strong>{data.manual_decision.outcome_label}</strong>
                </article>
              )}
            </section>

            {data.status !== "completed" && (
              <section className="panel">
                <h2>Next required action</h2>
                <p>{data.next_action_label}</p>
              </section>
            )}

            {data.status === "pending_action" && (
              <section className="panel">
                <h2>Acknowledge reconciliation</h2>
                {reviewMode && (
                  <div className="scanner-warning-panel">
                    <strong>Acknowledge reconciliation</strong>
                    <span>Route: {data.route_label}</span>
                    <p>{data.next_action_label}</p>
                    <p>This acknowledgement starts the next operational step. It does not resolve the case.</p>
                  </div>
                )}
                <button disabled={acknowledge.isPending} onClick={handleAcknowledge} type="button">
                  {reviewMode ? "Acknowledge case" : "Review acknowledgement"}
                </button>
                {reviewMode && (
                  <button className="scanner-secondary-button" onClick={() => setReviewMode(false)} type="button">
                    Cancel
                  </button>
                )}
              </section>
            )}

            {data.status === "in_progress" && (
              <section className="panel">
                <h2>Reconciliation in progress</h2>
                <p>
                  Acknowledged by {data.acknowledged_by_worker_code || "-"} at {formatDateTime(data.acknowledged_at)}
                </p>
                <p>{data.next_action_label}</p>
              </section>
            )}

            {data.manual_decision_required && (
              <section className="panel">
                <h2>Final manual reconciliation</h2>
                <p>Review the complete evidence chain and record one final outcome.</p>
                <label>
                  <span>Final reconciliation outcome</span>
                  <select onChange={(event) => setManualOutcome(event.target.value)} value={manualOutcome}>
                    <option value="">Select final outcome</option>
                    <option value="source_loss_confirmed">Source loss confirmed</option>
                    <option value="unresolved_loss_closed">Unresolved loss - cause not determined</option>
                    <option value="administrative_error">Administrative or process error</option>
                  </select>
                </label>
                <label>
                  <span>Final decision note</span>
                  <textarea
                    onChange={(event) => setDecisionNote(event.target.value)}
                    placeholder="Source search was completed and the remaining unit could not be located."
                    value={decisionNote}
                  />
                </label>
                {manualReviewMode && (
                  <div className="scanner-warning-panel">
                    <strong>Confirm final reconciliation</strong>
                    <span>Outcome: {selectedOutcomeLabel}</span>
                    <span>Decision note: {decisionNote}</span>
                    <p>
                      This will complete the reconciliation. No inventory will be changed automatically. The final decision
                      cannot be edited in this stage.
                    </p>
                  </div>
                )}
                <button
                  disabled={!manualOutcome || !decisionNote.trim() || completeManual.isPending}
                  onClick={handleCompleteManual}
                  type="button"
                >
                  {manualReviewMode ? "Complete reconciliation" : "Review final decision"}
                </button>
                {manualReviewMode && (
                  <button className="scanner-secondary-button" onClick={() => setManualReviewMode(false)} type="button">
                    Cancel
                  </button>
                )}
              </section>
            )}

            {data.manual_decision && (
              <section className="panel">
                <h2>Reconciliation completed</h2>
                <p>
                  Final outcome: <strong>{data.manual_decision.outcome_label}</strong>
                </p>
                <p>
                  Completed by {data.completed_by_worker_code || data.manual_decision.decided_by_worker_code || "-"} at{" "}
                  {formatDateTime(data.completed_at)}
                </p>
                <p>Decision note: {data.manual_decision.decision_note}</p>
              </section>
            )}

            {data.status === "completed" && !data.manual_decision && (
              <section className="panel">
                <h2>Reconciliation completed</h2>
                <p>All target shortage quantity was physically found at the source branch.</p>
                <p>
                  Completed by {data.completed_by_worker_code || "-"} at {formatDateTime(data.completed_at)}
                </p>
              </section>
            )}

            {data.source_stock_verification && (
              <section className="panel">
                <h2>Source stock verification</h2>
                <p>
                  <strong>{data.source_stock_verification.reference}</strong>
                </p>
                <p>
                  Status:{" "}
                  {sourceVerificationStatusLabel(
                    data.source_stock_verification.status,
                    data.source_stock_verification.status_label,
                  )}
                </p>
                <p>
                  Target {formatQuantity(data.source_stock_verification.total_target_quantity)} / Found{" "}
                  {formatQuantity(data.source_stock_verification.total_found_quantity)} / Source remaining{" "}
                  {formatQuantity(data.source_stock_verification.total_remaining_quantity)} / Source unresolved{" "}
                  {formatQuantity(data.source_stock_verification.total_unresolved_quantity)}
                </p>
                <Link to={`/wms/source-stock-verifications/${data.source_stock_verification.id}`}>
                  View source stock verification
                </Link>
              </section>
            )}

            <section className="panel">
              <h2>Source review</h2>
              <p>
                Finding: <strong>{data.source_review_finding_display}</strong>
              </p>
              <p>
                Completed by {data.source_review_completed_by_worker_code || "-"} at{" "}
                {formatDateTime(data.source_review_completed_at)}
              </p>
              {data.source_review_finding_note && <p>{data.source_review_finding_note}</p>}
            </section>

            <section className="panel">
              <h2>Final accounting</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Missing</th>
                      <th>Recovered</th>
                      <th>Confirmed shortage</th>
                      <th>Destination investigation remaining</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((line) => (
                      <tr key={line.id}>
                        <td>
                          <strong>{line.product_sku}</strong>
                          <br />
                          {line.product_name}
                        </td>
                        <td>{formatQuantity(line.missing_quantity)}</td>
                        <td>{formatQuantity(line.recovered_quantity)}</td>
                        <td>{formatQuantity(line.confirmed_shortage_quantity)}</td>
                        <td>{formatQuantity(line.remaining_quantity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
