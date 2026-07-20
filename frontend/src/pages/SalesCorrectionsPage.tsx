import { Link, useNavigate } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCreateSalesCorrection, useSalesCorrections } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

export function SalesCorrectionsPage() {
  const { activeBranchCode } = useActiveBranch();
  const navigate = useNavigate();
  const corrections = useSalesCorrections(activeBranchCode);
  const createCorrection = useCreateSalesCorrection();

  async function startDraft() {
    if (!activeBranchCode) return;
    const correction = await createCorrection.mutateAsync({ branch: activeBranchCode });
    navigate(`/wms/sales-corrections/${correction.id}`);
  }

  return (
    <>
      <PageHeader
        title="Sales Corrections"
        description={`Create and review sales corrections for working branch ${activeBranchCode || "-"}.`}
        action={<button className="status-pill status-pill--ok" disabled={!activeBranchCode || createCorrection.isPending} onClick={startDraft}>New Sales Correction</button>}
      />

      <DataState isError={corrections.isError} error={corrections.error as Error | null} isLoading={corrections.isLoading}>
        <section className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Reference</th>
                <th>Status</th>
                <th>Created by</th>
                <th>Confirmed by</th>
                <th>Confirmed</th>
                <th>Lines</th>
                <th>Total quantity</th>
              </tr>
            </thead>
            <tbody>
              {corrections.data?.results.map((correction) => (
                <tr key={correction.id}>
                  <td><Link className="table-link mono" to={`/wms/sales-corrections/${correction.id}`}>{correction.reference}</Link></td>
                  <td><span className="status-pill">{correction.status_label}</span></td>
                  <td>{correction.created_by_username || "-"}</td>
                  <td>{correction.confirmed_by_username || "-"}</td>
                  <td>{formatDate(correction.confirmed_at)}</td>
                  <td>{correction.line_count}</td>
                  <td>{correction.total_corrected_quantity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </DataState>
    </>
  );
}
