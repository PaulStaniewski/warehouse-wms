import { type FormEvent, useCallback, useState } from "react";
import { ArrowLeft, Camera } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  useBeginTransferDiscrepancySourceStockVerification,
  useCompleteTransferDiscrepancySourceSearch,
  useRecordTransferDiscrepancySourceStockFound,
  useTransferDiscrepancySourceStockVerification,
} from "../api/queries";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

type CameraMode = "product" | "location" | null;

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

export function SourceStockVerificationDetailPage() {
  const { id } = useParams();
  const verification = useTransferDiscrepancySourceStockVerification(id);
  const beginVerification = useBeginTransferDiscrepancySourceStockVerification();
  const recordFound = useRecordTransferDiscrepancySourceStockFound();
  const completeSearch = useCompleteTransferDiscrepancySourceSearch();
  const [productCode, setProductCode] = useState("");
  const [locationCode, setLocationCode] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [reviewMode, setReviewMode] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [completeSearchMode, setCompleteSearchMode] = useState(false);
  const [completeSearchOperationId, setCompleteSearchOperationId] = useState<string | null>(null);
  const [searchCompletionNote, setSearchCompletionNote] = useState("");
  const [cameraMode, setCameraMode] = useState<CameraMode>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = verification.data;

  async function handleBegin() {
    if (!data) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      await beginVerification.mutateAsync({ verificationId: data.id, workerCode: "DEMO" });
      await verification.refetch();
      setMessage("Source stock verification started.");
    } catch {
      setError("Could not begin source stock verification.");
    }
  }

  async function handleRecordFound(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!data) {
      return;
    }
    if (!reviewMode) {
      setOperationId((current) => current ?? crypto.randomUUID());
      setReviewMode(true);
      return;
    }
    if (!operationId) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      const response = await recordFound.mutateAsync({
        clientOperationId: operationId,
        destinationLocationCode: locationCode,
        productCode,
        quantity,
        verificationId: data.id,
        workerCode: "DEMO",
      });
      setProductCode("");
      setLocationCode("");
      setQuantity("1");
      setReviewMode(false);
      setOperationId(null);
      await verification.refetch();
      setMessage(
        Number(response.recovery.total_remaining_quantity) === 0
          ? "Source stock verification completed."
          : `Source stock restored. Remaining ${response.recovery.total_remaining_quantity}.`,
      );
    } catch {
      setError("Could not record found source stock.");
    }
  }

  async function handleCompleteSearch() {
    if (!data) {
      return;
    }
    if (!completeSearchMode) {
      setCompleteSearchOperationId((current) => current ?? crypto.randomUUID());
      setCompleteSearchMode(true);
      setMessage(null);
      setError(null);
      return;
    }
    if (!completeSearchOperationId) {
      return;
    }

    setMessage(null);
    setError(null);
    try {
      await completeSearch.mutateAsync({
        clientOperationId: completeSearchOperationId,
        searchCompletionNote,
        verificationId: data.id,
        workerCode: "DEMO",
      });
      setCompleteSearchMode(false);
      setCompleteSearchOperationId(null);
      await verification.refetch();
      setMessage("Source search completed. Reconciliation now requires manual action.");
    } catch {
      setError("Could not complete source search.");
    }
  }

  const handleCameraDetected = useCallback(
    (code: string) => {
      if (cameraMode === "product") {
        setProductCode(code);
      }
      if (cameraMode === "location") {
        setLocationCode(code);
      }
      setCameraMode(null);
    },
    [cameraMode],
  );

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/wms/source-stock-verifications">
          <ArrowLeft size={17} />
          Source stock verifications
        </Link>
      </div>

      <CameraBarcodeScanner isOpen={cameraMode !== null} onClose={() => setCameraMode(null)} onDetected={handleCameraDetected} />

      <PageHeader title={data?.reference ?? "Source stock verification"} description="Restore physically found source stock." />

      {message && <div className="scanner-message scanner-message--success">{message}</div>}
      {error && <div className="scanner-message scanner-message--error">{error}</div>}

      <DataState isLoading={verification.isLoading} isError={verification.isError} error={verification.error}>
        {data && (
          <>
            <section className="summary-grid">
              <article className="summary-card">
                <span>Status</span>
                <strong>{data.status_label}</strong>
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
                <span>Route</span>
                <strong>
                  {data.source_branch_code} to {data.destination_branch_code}
                </strong>
              </article>
              <article className="summary-card">
                <span>Target</span>
                <strong>{formatQuantity(data.total_target_quantity)}</strong>
              </article>
              <article className="summary-card">
                <span>Found at source</span>
                <strong>{formatQuantity(data.total_found_quantity)}</strong>
              </article>
              <article className="summary-card">
                <span>Remaining</span>
                <strong>{formatQuantity(data.total_remaining_quantity)}</strong>
              </article>
              <article className="summary-card">
                <span>Unresolved</span>
                <strong>{formatQuantity(data.total_unresolved_quantity)}</strong>
              </article>
            </section>

            <section className="panel">
              <h2>Next action</h2>
              <p>{data.next_action_label}</p>
            </section>

            {data.reconciliation_manual_decision && (
              <section className="panel">
                <h2>Final reconciliation outcome</h2>
                <p>
                  Reconciliation {data.reconciliation_reference} is {data.reconciliation_status}.
                </p>
                <p>
                  Final outcome: <strong>{data.reconciliation_manual_decision.outcome_label}</strong>
                </p>
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation}`}>View reconciliation</Link>
              </section>
            )}

            {data.status === "pending_verification" && (
              <div className="action-row">
                <button disabled={beginVerification.isPending} onClick={handleBegin} type="button">
                  {beginVerification.isPending ? "Starting..." : "Begin verification"}
                </button>
              </div>
            )}

            {data.status === "investigating" && Number(data.total_remaining_quantity) > 0 && (
              <>
                <form className="scanner-workflow-panel" onSubmit={handleRecordFound}>
                  <header>
                    <span>1</span>
                    <h2>Record found stock</h2>
                  </header>
                  <label>
                    <span>Scan found product</span>
                    <input onChange={(event) => setProductCode(event.target.value)} placeholder="FILTR-001" value={productCode} />
                  </label>
                  <button className="scanner-camera-button" onClick={() => setCameraMode("product")} type="button">
                    <Camera size={18} />
                    Scan product with camera
                  </button>
                  <label>
                    <span>Scan actual source location</span>
                    <input onChange={(event) => setLocationCode(event.target.value)} placeholder="B-01-01" value={locationCode} />
                  </label>
                  <button className="scanner-camera-button" onClick={() => setCameraMode("location")} type="button">
                    <Camera size={18} />
                    Scan location with camera
                  </button>
                  <label>
                    <span>Found quantity</span>
                    <input inputMode="numeric" min="1" onChange={(event) => setQuantity(event.target.value)} type="number" value={quantity} />
                  </label>
                  {reviewMode && (
                    <div className="scanner-warning-panel">
                      <strong>Confirm found source stock</strong>
                      <span>Product: {productCode}</span>
                      <span>Source branch: {data.source_branch_code}</span>
                      <span>Found at: {locationCode}</span>
                      <span>Quantity: {quantity}</span>
                      <p>This quantity will be restored to inventory at the scanned source location.</p>
                    </div>
                  )}
                  <button disabled={!productCode || !locationCode || !quantity || recordFound.isPending} type="submit">
                    {reviewMode ? "Restore found stock" : "Review restoration"}
                  </button>
                  {reviewMode && (
                    <button className="scanner-secondary-button" onClick={() => setReviewMode(false)} type="button">
                      Cancel
                    </button>
                  )}
                </form>

                <section className="scanner-workflow-panel">
                  <header>
                    <span>2</span>
                    <h2>Complete source search</h2>
                  </header>
                  {!completeSearchMode ? (
                    <>
                      <p>Use this when the source search is finished and the remaining quantity was not found.</p>
                      <button className="scanner-secondary-button" onClick={handleCompleteSearch} type="button">
                        Review source search completion
                      </button>
                    </>
                  ) : (
                    <>
                      <div className="scanner-warning-panel">
                        <strong>Complete source search</strong>
                        <span>Target: {formatQuantity(data.total_target_quantity)}</span>
                        <span>Found at source: {formatQuantity(data.total_found_quantity)}</span>
                        <span>Still unresolved: {formatQuantity(data.total_remaining_quantity)}</span>
                        <p>
                          The unresolved quantity will remain unchanged in inventory. The source stock verification will be
                          closed and the reconciliation will require manual action.
                        </p>
                      </div>
                      <label>
                        <span>Search completion note</span>
                        <textarea
                          onChange={(event) => setSearchCompletionNote(event.target.value)}
                          placeholder="Checked picking, staging and loading areas. Remaining stock was not found."
                          value={searchCompletionNote}
                        />
                      </label>
                      <button disabled={completeSearch.isPending} onClick={handleCompleteSearch} type="button">
                        {completeSearch.isPending ? "Completing..." : "Complete source search"}
                      </button>
                      <button className="scanner-secondary-button" onClick={() => setCompleteSearchMode(false)} type="button">
                        Cancel
                      </button>
                    </>
                  )}
                </section>
              </>
            )}

            {data.status === "completed" && (
              <section className="panel">
                <h2>Source stock verification completed</h2>
                <p>All target shortage quantity was found at the source branch and restored to inventory.</p>
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation}`}>View reconciliation</Link>
              </section>
            )}

            {data.status === "completed_unresolved" && (
              <section className="panel">
                <h2>Source search completed</h2>
                <p>The physical source search is closed with unresolved stock. Reconciliation requires manual action.</p>
                <dl className="detail-list">
                  <div>
                    <dt>Search completed by</dt>
                    <dd>{data.search_completed_by_worker_code || "-"}</dd>
                  </div>
                  <div>
                    <dt>Search completed at</dt>
                    <dd>{formatDateTime(data.search_completed_at)}</dd>
                  </div>
                  <div>
                    <dt>Search note</dt>
                    <dd>{data.search_completion_note || "-"}</dd>
                  </div>
                </dl>
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation}`}>View reconciliation</Link>
              </section>
            )}

            <section className="panel">
              <h2>Verification lines</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Target</th>
                      <th>Found</th>
                      <th>Remaining</th>
                      <th>Unresolved</th>
                      <th>Last found</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <strong>{item.product_sku}</strong>
                          <br />
                          {item.product_name}
                        </td>
                        <td>{formatQuantity(item.target_quantity)}</td>
                        <td>{formatQuantity(item.found_quantity)}</td>
                        <td>{formatQuantity(item.remaining_quantity)}</td>
                        <td>{formatQuantity(item.unresolved_quantity)}</td>
                        <td>{formatDateTime(item.last_found_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Found stock history</h2>
              {data.recoveries.length === 0 ? (
                <div className="state-box">No source stock has been recorded yet.</div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th>Quantity</th>
                        <th>Found at</th>
                        <th>Worker</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recoveries.map((recovery) => (
                        <tr key={recovery.id}>
                          <td>{recovery.product_sku}</td>
                          <td>{formatQuantity(recovery.quantity)}</td>
                          <td>{recovery.destination_location_code}</td>
                          <td>{recovery.worker_code || "-"}</td>
                          <td>{formatDateTime(recovery.recovered_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
