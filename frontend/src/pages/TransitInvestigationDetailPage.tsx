import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  useBeginTransferDiscrepancyTransitInvestigation,
  useCompleteTransferDiscrepancyTransitInvestigation,
  useTransferDiscrepancyTransitInvestigation,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string | number) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string | null | undefined) {
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

export function TransitInvestigationDetailPage() {
  const { id } = useParams();
  const investigation = useTransferDiscrepancyTransitInvestigation(id);
  const beginInvestigation = useBeginTransferDiscrepancyTransitInvestigation();
  const completeInvestigation = useCompleteTransferDiscrepancyTransitInvestigation();
  const [finding, setFinding] = useState("");
  const [findingNote, setFindingNote] = useState("");
  const [reviewMode, setReviewMode] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = investigation.data;

  async function handleBegin() {
    if (!data) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await beginInvestigation.mutateAsync({ investigationId: data.id, workerCode: "DEMO" });
      await investigation.refetch();
      setMessage("Transit investigation started.");
    } catch {
      setError("Could not begin transit investigation.");
    }
  }

  async function handleComplete() {
    if (!data) {
      return;
    }
    if (!reviewMode) {
      setOperationId((current) => current ?? crypto.randomUUID());
      setReviewMode(true);
      setMessage(null);
      setError(null);
      return;
    }
    if (!operationId) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await completeInvestigation.mutateAsync({
        clientOperationId: operationId,
        finding,
        findingNote,
        investigationId: data.id,
        workerCode: "DEMO",
      });
      setReviewMode(false);
      setOperationId(null);
      await investigation.refetch();
      setMessage("Transit investigation completed. Reconciliation now requires manual action.");
    } catch {
      setError("Could not complete transit investigation.");
    }
  }

  const findingLabel =
    finding === "transit_irregularity_found"
      ? "Transit irregularity found"
      : finding === "no_transit_irregularity_identified"
        ? "No transit irregularity identified"
        : finding === "inconclusive"
          ? "Inconclusive"
          : "";

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/wms/transit-investigations">
          <ArrowLeft size={17} />
          Transit investigations
        </Link>
      </div>

      <PageHeader title={data?.reference ?? "Transit investigation"} description="Review transfer, route and receiving evidence." />

      {message && <div className="scanner-message scanner-message--success">{message}</div>}
      {error && <div className="scanner-message scanner-message--error">{error}</div>}

      <DataState isLoading={investigation.isLoading} isError={investigation.isError} error={investigation.error}>
        {data && (
          <>
            <section className="summary-grid">
              <article className="summary-card">
                <span>Status</span>
                <strong>{data.status_label}</strong>
              </article>
              <article className="summary-card">
                <span>Finding</span>
                <strong>{data.finding_label || "-"}</strong>
              </article>
              <article className="summary-card">
                <span>Reconciliation</span>
                <strong>{data.reconciliation_reference}</strong>
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
            </section>

            <section className="panel">
              <h2>Next action</h2>
              <p>{data.next_action_label}</p>
            </section>

            {data.status === "pending_investigation" && (
              <div className="action-row">
                <button disabled={beginInvestigation.isPending} onClick={handleBegin} type="button">
                  {beginInvestigation.isPending ? "Starting..." : "Begin investigation"}
                </button>
              </div>
            )}

            {data.status === "investigating" && (
              <section className="panel">
                <h2>Complete transit investigation</h2>
                <label>
                  <span>Finding</span>
                  <select onChange={(event) => setFinding(event.target.value)} value={finding}>
                    <option value="">Select finding</option>
                    <option value="transit_irregularity_found">Transit irregularity found</option>
                    <option value="no_transit_irregularity_identified">No transit irregularity identified</option>
                    <option value="inconclusive">Inconclusive</option>
                  </select>
                </label>
                <label>
                  <span>Investigation note</span>
                  <textarea
                    onChange={(event) => setFindingNote(event.target.value)}
                    placeholder="Dispatch and route records are complete, but no item-level handoff evidence exists."
                    value={findingNote}
                  />
                </label>
                {reviewMode && (
                  <div className="scanner-warning-panel">
                    <strong>Confirm transit investigation finding</strong>
                    <span>Finding: {findingLabel}</span>
                    <span>Investigation note: {findingNote}</span>
                    <p>
                      This will complete the transit investigation. No inventory will be changed automatically. The
                      reconciliation will require a final manual decision.
                    </p>
                  </div>
                )}
                <button disabled={!finding || !findingNote.trim() || completeInvestigation.isPending} onClick={handleComplete} type="button">
                  {reviewMode ? "Complete transit investigation" : "Review finding"}
                </button>
                {reviewMode && (
                  <button className="scanner-secondary-button" onClick={() => setReviewMode(false)} type="button">
                    Cancel
                  </button>
                )}
              </section>
            )}

            {data.status === "completed" && (
              <section className="panel">
                <h2>Transit investigation completed</h2>
                <p>
                  Finding: <strong>{data.finding_label}</strong>
                </p>
                <p>
                  Completed by {data.completed_by_worker_code || "-"} at {formatDateTime(data.completed_at)}
                </p>
                <p>Investigation note: {data.finding_note}</p>
                <p>Reconciliation status: {data.reconciliation_status_label}</p>
                {data.reconciliation_manual_decision && (
                  <p>
                    Final outcome: <strong>{data.reconciliation_manual_decision.outcome_label}</strong>
                  </p>
                )}
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation}`}>View reconciliation</Link>
              </section>
            )}

            <section className="panel">
              <h2>Transfer summary</h2>
              <dl className="detail-list">
                <div>
                  <dt>Route</dt>
                  <dd>
                    {data.source_branch_code} to {data.destination_branch_code}
                  </dd>
                </div>
                <div>
                  <dt>Transfer status</dt>
                  <dd>{data.transfer_status}</dd>
                </div>
                <div>
                  <dt>Pallet status</dt>
                  <dd>{data.pallet_status}</dd>
                </div>
                <div>
                  <dt>Pallet closed</dt>
                  <dd>{formatDateTime(data.transfer_summary.pallet_closed_at)}</dd>
                </div>
              </dl>
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
                        <td>
                          <strong>{item.product_sku}</strong>
                          <br />
                          {item.product_name}
                        </td>
                        <td>{formatQuantity(item.expected_quantity ?? 0)}</td>
                        <td>{item.pallet_code}</td>
                        <td>{formatDateTime(item.released_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Transit / route evidence</h2>
              {data.transit_route_evidence.length === 0 ? (
                <div className="state-box">No dedicated transit route events are recorded for this transfer.</div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Event</th>
                        <th>Reference</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.transit_route_evidence.map((event) => (
                        <tr key={`${event.label}-${event.timestamp}`}>
                          <td>{event.label}</td>
                          <td>{event.reference}</td>
                          <td>{formatDateTime(event.timestamp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
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
                    {data.destination_receiving_evidence.map((item, index) => (
                      <tr key={`${item.product_sku}-${index}`}>
                        <td>{item.product_sku}</td>
                        <td>{formatQuantity(item.quantity ?? 0)}</td>
                        <td>{item.destination_location_code}</td>
                        <td>{item.worker_code || "-"}</td>
                        <td>{formatDateTime(item.scanned_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Destination investigation outcome</h2>
              <p>
                Posted to UNCONFIRMED {formatQuantity(data.destination_investigation_outcome.posted_to_unconfirmed)} /
                Destination recovered {formatQuantity(data.destination_investigation_outcome.destination_recovered)} /
                Confirmed shortage {formatQuantity(data.destination_investigation_outcome.confirmed_shortage)} /
                Destination remaining {formatQuantity(data.destination_investigation_outcome.destination_remaining)}
              </p>
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
                      <th>Destination remaining</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.final_accounting_lines.map((line) => (
                      <tr key={line.id}>
                        <td>{line.product_sku}</td>
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
