import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { useTransferDiscrepancy } from "../api/queries";
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
  const data = discrepancy.data;

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
            </section>

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
          </>
        )}
      </DataState>
    </>
  );
}
