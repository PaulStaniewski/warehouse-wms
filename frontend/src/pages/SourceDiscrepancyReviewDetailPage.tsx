import { type FormEvent, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  useBeginTransferDiscrepancySourceReview,
  useCompleteTransferDiscrepancySourceReview,
  useTransferDiscrepancySourceReview,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { sourceVerificationStatusLabel } from "../types/display";

const findingOptions = [
  { value: "source_shortage_found", label: "Source shortage found" },
  { value: "dispatch_evidence_matches", label: "Dispatch evidence matches expected quantity" },
  { value: "inconclusive", label: "Inconclusive" },
];

function formatQuantity(value: string) {
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

export function SourceDiscrepancyReviewDetailPage() {
  const { id } = useParams();
  const review = useTransferDiscrepancySourceReview(id);
  const beginReview = useBeginTransferDiscrepancySourceReview();
  const completeReview = useCompleteTransferDiscrepancySourceReview();
  const [finding, setFinding] = useState("source_shortage_found");
  const [findingNote, setFindingNote] = useState("");
  const [reviewMode, setReviewMode] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = review.data;

  async function handleBeginReview() {
    if (!data) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await beginReview.mutateAsync({ reviewId: data.id, workerCode: "DEMO" });
      await review.refetch();
      setMessage("Source review started.");
    } catch {
      setError("Could not begin source review.");
    }
  }

  async function handleCompleteReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
      await completeReview.mutateAsync({
        clientOperationId: crypto.randomUUID(),
        finding,
        findingNote,
        reviewId: data.id,
        workerCode: "DEMO",
      });
      setReviewMode(false);
      await review.refetch();
      setMessage("Source review completed.");
    } catch {
      setError("Could not complete source review.");
    }
  }

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/wms/source-discrepancy-reviews">
          <ArrowLeft size={17} />
          Source reviews
        </Link>
      </div>

      <PageHeader title={data?.reference ?? "Source review"} description="Source-branch investigation for confirmed shortage." />

      {message && <div className="scanner-message scanner-message--success">{message}</div>}
      {error && <div className="scanner-message scanner-message--error">{error}</div>}

      <DataState isLoading={review.isLoading} isError={review.isError} error={review.error}>
        {data && (
          <>
            <section className="summary-grid">
              <article className="summary-card">
                <span>Status</span>
                <strong>{data.status}</strong>
              </article>
              <article className="summary-card">
                <span>Discrepancy</span>
                <strong>{data.discrepancy_reference}</strong>
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
                <span>Route</span>
                <strong>
                  {data.source_branch_code} to {data.destination_branch_code}
                </strong>
              </article>
              <article className="summary-card">
                <span>Confirmed shortage</span>
                <strong>{formatQuantity(data.total_confirmed_shortage_quantity)}</strong>
              </article>
            </section>

            {data.status === "pending_review" && (
              <div className="action-row">
                <button disabled={beginReview.isPending} onClick={handleBeginReview} type="button">
                  {beginReview.isPending ? "Starting..." : "Begin review"}
                </button>
              </div>
            )}

            {data.status === "investigating" && (
              <form className="panel" onSubmit={handleCompleteReview}>
                <h2>Complete source review</h2>
                <label>
                  <span>Finding</span>
                  <select onChange={(event) => setFinding(event.target.value)} value={finding}>
                    {findingOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Investigation note</span>
                  <textarea onChange={(event) => setFindingNote(event.target.value)} value={findingNote} />
                </label>
                {reviewMode && (
                  <div className="scanner-warning-panel">
                    <strong>Confirm review finding</strong>
                    <span>{findingOptions.find((option) => option.value === finding)?.label}</span>
                    <p>This will complete the source review. No inventory will be changed automatically.</p>
                  </div>
                )}
                <button disabled={completeReview.isPending} type="submit">
                  {reviewMode ? "Complete source review" : "Review finding"}
                </button>
                {reviewMode && (
                  <button className="scanner-secondary-button" onClick={() => setReviewMode(false)} type="button">
                    Cancel
                  </button>
                )}
              </form>
            )}

            {data.status === "completed" && (
              <section className="panel">
                <h2>Source review completed</h2>
                <p>
                  Finding: <strong>{data.finding_display}</strong>
                </p>
                <p>
                  Completed by {data.completed_by_worker_code || "-"} at {formatDateTime(data.completed_at)}
                </p>
                {data.finding_note && <p>{data.finding_note}</p>}
              </section>
            )}

            {data.reconciliation && (
              <section className="panel">
                <h2>Reconciliation</h2>
                <p>
                  <strong>{data.reconciliation.reference}</strong>
                </p>
                <p>Route: {data.reconciliation.route_label}</p>
                <p>Status: {data.reconciliation.status}</p>
                {data.reconciliation.manual_decision && <p>Final outcome: {data.reconciliation.manual_decision.outcome_label}</p>}
                <p>{data.reconciliation.next_action_label}</p>
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation.id}`}>View reconciliation</Link>
              </section>
            )}

            {data.reconciliation?.source_stock_verification && (
              <section className="panel">
                <h2>Source stock verification</h2>
                <p>
                  <strong>{data.reconciliation.source_stock_verification.reference}</strong>
                </p>
                <p>
                  Status:{" "}
                  {sourceVerificationStatusLabel(
                    data.reconciliation.source_stock_verification.status,
                    data.reconciliation.source_stock_verification.status_label,
                  )}
                </p>
                <p>
                  Found at source: {formatQuantity(String(data.reconciliation.source_stock_verification.total_found_quantity))} / Source
                  remaining: {formatQuantity(String(data.reconciliation.source_stock_verification.total_remaining_quantity))} / Source
                  unresolved:{" "}
                  {formatQuantity(String(data.reconciliation.source_stock_verification.total_unresolved_quantity))}
                </p>
                <Link to={`/wms/source-stock-verifications/${data.reconciliation.source_stock_verification.id}`}>
                  View source stock verification
                </Link>
              </section>
            )}

            <section className="panel">
              <h2>Final accounting</h2>
              <div className="summary-grid">
                <article className="summary-card">
                  <span>Expected</span>
                  <strong>{formatQuantity(data.total_expected_quantity)}</strong>
                </article>
                <article className="summary-card">
                  <span>Received</span>
                  <strong>{formatQuantity(data.total_received_quantity)}</strong>
                </article>
                <article className="summary-card">
                  <span>Missing</span>
                  <strong>{formatQuantity(data.total_missing_quantity)}</strong>
                </article>
                <article className="summary-card">
                  <span>Recovered</span>
                  <strong>{formatQuantity(data.total_recovered_quantity)}</strong>
                </article>
                <article className="summary-card">
                  <span>Confirmed shortage</span>
                  <strong>{formatQuantity(data.total_confirmed_shortage_quantity)}</strong>
                </article>
                <article className="summary-card">
                  <span>Destination remaining</span>
                  <strong>{formatQuantity(data.total_remaining_quantity)}</strong>
                </article>
              </div>
            </section>

            <section className="panel">
              <h2>Source dispatch evidence</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Expected</th>
                      <th>Pallet</th>
                      <th>Released</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.source_dispatch_evidence.map((item) => (
                      <tr key={`${item.product_sku}-${item.pallet_code}`}>
                        <td>{item.product_sku}</td>
                        <td>{formatQuantity(item.expected_quantity ?? "0")}</td>
                        <td>{item.pallet_code}</td>
                        <td>{formatDateTime(item.released_at ?? null)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Destination receiving evidence</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Quantity</th>
                      <th>Location</th>
                      <th>Worker</th>
                      <th>Scanned</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.destination_receiving_evidence.map((scan, index) => (
                      <tr key={`${scan.product_sku}-${index}`}>
                        <td>{scan.product_sku}</td>
                        <td>{formatQuantity(scan.quantity ?? "0")}</td>
                        <td>{scan.destination_location_code}</td>
                        <td>{scan.worker_code || "-"}</td>
                        <td>{formatDateTime(scan.scanned_at ?? null)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Confirmed shortages</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Quantity</th>
                      <th>Removed from</th>
                      <th>Worker</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.shortage_confirmations.map((confirmation) => (
                      <tr key={confirmation.id}>
                        <td>{confirmation.product_sku}</td>
                        <td>{formatQuantity(confirmation.quantity)}</td>
                        <td>{confirmation.unconfirmed_location_code}</td>
                        <td>{confirmation.worker_code || "-"}</td>
                        <td>{formatDateTime(confirmation.confirmed_at)}</td>
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
