import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import {
  useConfirmTransferDiscrepancyShortage,
  usePrintTransferDiscrepancyReport,
  useRecoverTransferDiscrepancyItem,
  useTransferDiscrepancy,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

export function DiscrepancyDetailPage() {
  const { id } = useParams();
  const discrepancy = useTransferDiscrepancy(id);
  const printReport = usePrintTransferDiscrepancyReport();
  const recoverItem = useRecoverTransferDiscrepancyItem();
  const confirmShortage = useConfirmTransferDiscrepancyShortage();
  const data = discrepancy.data;

  async function handlePrintReport() {
    if (!data) {
      return;
    }
    const printerCode = window.prompt("Printer code", data.last_report_printer_code || "ZEBRA-01")?.trim();
    if (!printerCode) {
      return;
    }
    await printReport.mutateAsync({ discrepancyId: data.id, printerCode, workerCode: "DEMO" });
    await discrepancy.refetch();
  }

  async function handleRecoverItem() {
    if (!data) {
      return;
    }
    const productCode = window.prompt("Product code")?.trim();
    if (!productCode) {
      return;
    }
    const destinationLocationCode = window.prompt("Actual location")?.trim();
    if (!destinationLocationCode) {
      return;
    }
    const quantity = window.prompt("Quantity", "1")?.trim();
    if (!quantity) {
      return;
    }
    await recoverItem.mutateAsync({
      clientOperationId: crypto.randomUUID(),
      destinationLocationCode,
      discrepancyId: data.id,
      productCode,
      quantity,
      workerCode: "DEMO",
    });
    await discrepancy.refetch();
  }

  async function handleConfirmShortage() {
    if (!data) {
      return;
    }
    const productCode = window.prompt("Product code")?.trim();
    if (!productCode) {
      return;
    }
    const quantity = window.prompt("Quantity to confirm as missing", "1")?.trim();
    if (!quantity) {
      return;
    }
    const confirmed = window.confirm(
      "This quantity will be removed from UNCONFIRMED inventory and recorded as a confirmed shortage.",
    );
    if (!confirmed) {
      return;
    }
    await confirmShortage.mutateAsync({
      clientOperationId: crypto.randomUUID(),
      discrepancyId: data.id,
      productCode,
      quantity,
      workerCode: "DEMO",
    });
    await discrepancy.refetch();
  }

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/wms/discrepancies">
          <ArrowLeft size={17} />
          Discrepancies
        </Link>
      </div>

      <PageHeader title={data?.reference ?? "Discrepancy"} description="Transfer pallet discrepancy evidence." />

      <DataState isLoading={discrepancy.isLoading} isError={discrepancy.isError} error={discrepancy.error}>
        {data && (
          <>
            <section className="summary-grid">
              <article className="summary-card">
                <span>Pallet</span>
                <strong>{data.pallet_code}</strong>
              </article>
              <article className="summary-card">
                <span>Transfer</span>
                <strong>{data.transfer_reference}</strong>
              </article>
              <article className="summary-card">
                <span>Branches</span>
                <strong>
                  {data.source_branch_code} - {data.destination_branch_code}
                </strong>
              </article>
              <article className="summary-card">
                <span>Status</span>
                <strong>{data.status}</strong>
              </article>
              <article className="summary-card">
                <span>Report</span>
                <strong>{data.report_printed_at ? "Printed" : "Not printed"}</strong>
              </article>
              <article className="summary-card">
                <span>Print count</span>
                <strong>{data.report_print_count}</strong>
              </article>
              <article className="summary-card">
                <span>Last printer</span>
                <strong>{data.last_report_printer_code || "-"}</strong>
              </article>
              <article className="summary-card">
                <span>UNCONFIRMED posting</span>
                <strong>{data.shortage_posted_at ? "Posted" : "Not posted"}</strong>
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
                <span>Remaining</span>
                <strong>{formatQuantity(data.total_remaining_quantity)}</strong>
              </article>
              {data.confirmed_shortage_at && (
                <article className="summary-card">
                  <span>Confirmed at</span>
                  <strong>{formatDateTime(data.confirmed_shortage_at)}</strong>
                </article>
              )}
              {data.confirmed_shortage_by_worker_code && (
                <article className="summary-card">
                  <span>Confirmed by</span>
                  <strong>{data.confirmed_shortage_by_worker_code}</strong>
                </article>
              )}
              {data.source_review && (
                <article className="summary-card">
                  <span>Source review</span>
                  <strong>{data.source_review.status}</strong>
                  <small>
                    {data.source_review.reference}
                    {data.source_review.finding_display ? ` / ${data.source_review.finding_display}` : ""}
                  </small>
                </article>
              )}
              {data.reconciliation && (
                <article className="summary-card">
                  <span>Reconciliation</span>
                  <strong>{data.reconciliation.status}</strong>
                  <small>
                    {data.reconciliation.reference} / {data.reconciliation.route_label}
                    {data.reconciliation.manual_decision ? ` / ${data.reconciliation.manual_decision.outcome_label}` : ""}
                  </small>
                </article>
              )}
              {data.reconciliation?.source_stock_verification && (
                <article className="summary-card">
                  <span>Source stock verification</span>
                  <strong>{data.reconciliation.source_stock_verification.status}</strong>
                  <small>
                    Found {formatQuantity(String(data.reconciliation.source_stock_verification.total_found_quantity))} / Remaining{" "}
                    {formatQuantity(String(data.reconciliation.source_stock_verification.total_remaining_quantity))} / Unresolved{" "}
                    {formatQuantity(String(data.reconciliation.source_stock_verification.total_unresolved_quantity))}
                  </small>
                </article>
              )}
              {data.resolved_at && (
                <article className="summary-card">
                  <span>Resolved at</span>
                  <strong>{formatDateTime(data.resolved_at)}</strong>
                </article>
              )}
            </section>

            <div className="action-row">
              <button disabled={printReport.isPending} onClick={handlePrintReport} type="button">
                {data.report_printed_at ? "Reprint report" : "Print report"}
              </button>
              {data.status === "investigating" && Number(data.total_remaining_quantity) > 0 && (
                <button disabled={recoverItem.isPending} onClick={handleRecoverItem} type="button">
                  Record found item
                </button>
              )}
              {data.status === "investigating" && Number(data.total_remaining_quantity) > 0 && (
                <button disabled={confirmShortage.isPending} onClick={handleConfirmShortage} type="button">
                  Confirm shortage
                </button>
              )}
              <Link to={`/wms/discrepancies/${data.id}/report`}>Open printable report</Link>
              {data.source_review && (
                <Link to={`/wms/source-discrepancy-reviews/${data.source_review.id}`}>View source review</Link>
              )}
              {data.reconciliation && (
                <Link to={`/wms/discrepancy-reconciliations/${data.reconciliation.id}`}>View reconciliation</Link>
              )}
              {data.reconciliation?.source_stock_verification && (
                <Link to={`/wms/source-stock-verifications/${data.reconciliation.source_stock_verification.id}`}>
                  View source stock verification
                </Link>
              )}
            </div>

            <section className="panel">
              <h2>Discrepancy lines</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Type</th>
                      <th>Expected</th>
                      <th>Received</th>
                      <th>Difference</th>
                        <th>Shortage</th>
                        <th>Posted to UNCONFIRMED</th>
                      <th>Recovered</th>
                      <th>Confirmed shortage</th>
                      <th>Remaining</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((line) => (
                      <tr key={line.id}>
                        <td>
                          <strong>{line.product_sku}</strong>
                          <br />
                          {line.product_name}
                        </td>
                        <td>{line.discrepancy_type}</td>
                        <td>{formatQuantity(line.expected_quantity)}</td>
                        <td>{formatQuantity(line.received_quantity)}</td>
                        <td>{formatQuantity(line.difference_quantity)}</td>
                        <td>{formatQuantity(line.discrepancy_quantity)}</td>
                        <td>{formatQuantity(line.posted_to_unconfirmed_quantity)}</td>
                        <td>{formatQuantity(line.recovered_quantity)}</td>
                        <td>{formatQuantity(line.confirmed_shortage_quantity)}</td>
                        <td>{formatQuantity(line.remaining_quantity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <h2>Receiving scan history</h2>
              {data.items.every((line) => line.scan_history.length === 0) ? (
                <div className="state-box">No receiving scans found for affected products.</div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th>Location</th>
                        <th>Quantity</th>
                        <th>Worker</th>
                        <th>Scanned</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.flatMap((line) =>
                        line.scan_history.map((scan) => (
                          <tr key={`${line.id}-${scan.id}`}>
                            <td>{scan.product_sku}</td>
                            <td>{scan.destination_location_code}</td>
                            <td>{formatQuantity(scan.quantity)}</td>
                            <td>{scan.worker_code || "-"}</td>
                            <td>{formatDateTime(scan.scanned_at)}</td>
                          </tr>
                        )),
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="panel">
              <h2>Recovered items</h2>
              {data.recoveries.length === 0 ? (
                <div className="state-box">No recovered items recorded.</div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th>Quantity</th>
                        <th>From</th>
                        <th>To</th>
                        <th>Worker</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recoveries.map((recovery) => (
                        <tr key={recovery.id}>
                          <td>{recovery.product_sku}</td>
                          <td>{formatQuantity(recovery.quantity)}</td>
                          <td>{recovery.source_location_code}</td>
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

            <section className="panel">
              <h2>Confirmed shortages</h2>
              {data.shortage_confirmations.length === 0 ? (
                <div className="state-box">No shortage confirmations recorded.</div>
              ) : (
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
              )}
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
