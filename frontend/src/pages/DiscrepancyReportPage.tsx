import { Printer } from "lucide-react";
import { useParams } from "react-router-dom";

import { useTransferDiscrepancy } from "../api/queries";
import { DataState } from "../components/DataState";

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

export function DiscrepancyReportPage() {
  const { id } = useParams();
  const discrepancy = useTransferDiscrepancy(id);
  const data = discrepancy.data;

  const expected = data?.items.reduce((sum, item) => sum + Number(item.expected_quantity), 0) ?? 0;
  const received = data?.items.reduce((sum, item) => sum + Number(item.received_quantity), 0) ?? 0;
  const missing = data?.items.reduce((sum, item) => sum + Number(item.discrepancy_quantity), 0) ?? 0;

  return (
    <DataState isLoading={discrepancy.isLoading} isError={discrepancy.isError} error={discrepancy.error}>
      {data && (
        <section className="route-document">
          <header className="route-document-header">
            <div>
              <p>Discrepancy report</p>
              <h1>{data.reference}</h1>
              <span>
                {data.source_branch_code} to {data.destination_branch_code}
              </span>
            </div>
            <button onClick={() => window.print()} type="button">
              <Printer size={18} />
              Print
            </button>
          </header>

          <dl className="route-document-meta">
            <div>
              <dt>Pallet</dt>
              <dd>{data.pallet_code}</dd>
            </div>
            <div>
              <dt>Transfer</dt>
              <dd>{data.transfer_reference}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDateTime(data.created_at)}</dd>
            </div>
            <div>
              <dt>Report printed</dt>
              <dd>{formatDateTime(data.report_printed_at)}</dd>
            </div>
            <div>
              <dt>Printer</dt>
              <dd>{data.last_report_printer_code || "-"}</dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>{data.created_by_worker_code || "-"}</dd>
            </div>
          </dl>

          <h2>Summary</h2>
          <p>
            Expected: {formatQuantity(String(expected))} / Received: {formatQuantity(String(received))} / Missing:{" "}
            {formatQuantity(String(missing))} / Affected lines: {data.line_count}
          </p>

          <h2>Investigation status</h2>
          <p>
            Status: {data.status} / Posted to UNCONFIRMED:{" "}
            {formatQuantity(data.total_posted_to_unconfirmed_quantity)} / Recovered:{" "}
            {formatQuantity(data.total_recovered_quantity)} / Confirmed shortage:{" "}
            {formatQuantity(data.total_confirmed_shortage_quantity)} / Remaining: {formatQuantity(data.total_remaining_quantity)}
          </p>

          <h2>Discrepancy lines</h2>
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th>Expected</th>
                <th>Received</th>
                <th>Difference</th>
                <th>Missing</th>
                <th>Posted</th>
                <th>Recovered</th>
                <th>Confirmed shortage</th>
                <th>Remaining</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.product_sku}</td>
                  <td>{item.product_name}</td>
                  <td>{formatQuantity(item.expected_quantity)}</td>
                  <td>{formatQuantity(item.received_quantity)}</td>
                  <td>{formatQuantity(item.difference_quantity)}</td>
                  <td>{formatQuantity(item.discrepancy_quantity)}</td>
                  <td>{formatQuantity(item.posted_to_unconfirmed_quantity)}</td>
                  <td>{formatQuantity(item.recovered_quantity)}</td>
                  <td>{formatQuantity(item.confirmed_shortage_quantity)}</td>
                  <td>{formatQuantity(item.remaining_quantity)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2>Recovered items</h2>
          <table>
            <thead>
              <tr>
                <th>SKU</th>
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

          <h2>Confirmed shortages</h2>
          <table>
            <thead>
              <tr>
                <th>SKU</th>
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

          {data.source_review && (
            <>
              <h2>Source review</h2>
              <p>
                Reference: {data.source_review.reference} / Status: {data.source_review.status}
                {data.source_review.finding_display ? ` / Finding: ${data.source_review.finding_display}` : ""}
              </p>
            </>
          )}

          {data.reconciliation && (
            <>
              <h2>Reconciliation</h2>
              <p>
                Reference: {data.reconciliation.reference} / Route: {data.reconciliation.route_label} / Status:{" "}
                {data.reconciliation.status}
              </p>
            </>
          )}

          {data.reconciliation?.source_stock_verification && (
            <>
              <h2>Source stock verification</h2>
              <p>
                Reference: {data.reconciliation.source_stock_verification.reference} / Status:{" "}
                {data.reconciliation.source_stock_verification.status} / Target:{" "}
                {formatQuantity(String(data.reconciliation.source_stock_verification.total_target_quantity))} / Found at source:{" "}
                {formatQuantity(String(data.reconciliation.source_stock_verification.total_found_quantity))} / Remaining:{" "}
                {formatQuantity(String(data.reconciliation.source_stock_verification.total_remaining_quantity))} / Unresolved:{" "}
                {formatQuantity(String(data.reconciliation.source_stock_verification.total_unresolved_quantity))}
              </p>
              {data.reconciliation.source_stock_verification.search_completed_at && (
                <p>
                  Search completed by: {data.reconciliation.source_stock_verification.search_completed_by_worker_code || "-"} / Search
                  completed at: {formatDateTime(data.reconciliation.source_stock_verification.search_completed_at)}
                </p>
              )}
              {data.reconciliation.source_stock_verification.search_completion_note && (
                <p>Search note: {data.reconciliation.source_stock_verification.search_completion_note}</p>
              )}
            </>
          )}

          {data.reconciliation?.manual_decision && (
            <>
              <h2>Final reconciliation</h2>
              <p>Outcome: {data.reconciliation.manual_decision.outcome_label}</p>
              <p>
                Decided by: {data.reconciliation.manual_decision.decided_by_worker_code || "-"} / Decided at:{" "}
                {formatDateTime(data.reconciliation.manual_decision.decided_at)}
              </p>
              <p>Decision note: {data.reconciliation.manual_decision.decision_note}</p>
            </>
          )}

          <h2>Receiving scan evidence</h2>
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Quantity</th>
                <th>Location</th>
                <th>Worker</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {data.items.flatMap((item) =>
                item.scan_history.map((scan) => (
                  <tr key={`${item.id}-${scan.id}`}>
                    <td>{scan.product_sku}</td>
                    <td>{formatQuantity(scan.quantity)}</td>
                    <td>{scan.destination_location_code}</td>
                    <td>{scan.worker_code || "-"}</td>
                    <td>{formatDateTime(scan.scanned_at)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </section>
      )}
    </DataState>
  );
}
