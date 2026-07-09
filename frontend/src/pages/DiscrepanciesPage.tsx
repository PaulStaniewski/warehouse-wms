import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useTransferDiscrepancies } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function formatQuantity(value: string) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

export function DiscrepanciesPage() {
  const { activeBranchCode } = useActiveBranch();
  const discrepancies = useTransferDiscrepancies(activeBranchCode);
  const rows = discrepancies.data?.results ?? [];

  return (
    <>
      <PageHeader title="Discrepancies" description="Read-only register of inter-branch transfer pallet shortages." />

      <DataState isLoading={discrepancies.isLoading} isError={discrepancies.isError} error={discrepancies.error}>
        {rows.length === 0 ? (
          <div className="state-box">No transfer discrepancies found.</div>
        ) : (
          <section className="panel">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Pallet</th>
                    <th>Transfer</th>
                    <th>Route</th>
                    <th>Status</th>
                    <th>Lines</th>
                    <th>Quantity</th>
                    <th>Created</th>
                    <th>Worker</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link to={`/wms/discrepancies/${item.id}`}>
                          <strong>{item.reference}</strong>
                        </Link>
                      </td>
                      <td>{item.pallet_code}</td>
                      <td>{item.transfer_reference}</td>
                      <td>
                        {item.source_branch_code} - {item.destination_branch_code}
                      </td>
                      <td>{item.status}</td>
                      <td>{item.line_count}</td>
                      <td>{formatQuantity(item.total_discrepancy_quantity)}</td>
                      <td>{formatDateTime(item.created_at)}</td>
                      <td>{item.created_by_worker_code || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </DataState>
    </>
  );
}
