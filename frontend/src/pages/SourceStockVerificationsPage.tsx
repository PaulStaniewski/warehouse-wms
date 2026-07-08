import { Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useTransferDiscrepancySourceStockVerifications } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string | number) {
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

export function SourceStockVerificationsPage() {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const verifications = useTransferDiscrepancySourceStockVerifications(status, search);
  const rows = verifications.data?.results ?? [];

  return (
    <>
      <PageHeader title="Source Stock Verifications" description="Physically found source stock for discrepancy reconciliation." />

      <section className="event-filter-panel">
        <label>
          <span>Status</span>
          <select onChange={(event) => setStatus(event.target.value)} value={status}>
            <option value="">All statuses</option>
            <option value="pending_verification">Pending verification</option>
            <option value="investigating">Investigating</option>
            <option value="completed">Completed</option>
            <option value="completed_unresolved">Completed with unresolved stock</option>
          </select>
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Verification, reconciliation, discrepancy, pallet or product"
              value={search}
            />
          </div>
        </label>
      </section>

      <DataState isLoading={verifications.isLoading} isError={verifications.isError} error={verifications.error}>
        <section className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Reconciliation</th>
                  <th>Discrepancy</th>
                  <th>Pallet</th>
                  <th>Transfer</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Target</th>
                  <th>Found</th>
                  <th>Source remaining</th>
                  <th>Source unresolved</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/wms/source-stock-verifications/${item.id}`}>{item.reference}</Link>
                    </td>
                    <td>{item.reconciliation_reference}</td>
                    <td>{item.discrepancy_reference}</td>
                    <td>{item.pallet_code}</td>
                    <td>{item.transfer_reference}</td>
                    <td>{item.source_branch_code}</td>
                    <td>{item.status_label}</td>
                    <td>{formatQuantity(item.total_target_quantity)}</td>
                    <td>{formatQuantity(item.total_found_quantity)}</td>
                    <td>{formatQuantity(item.total_remaining_quantity)}</td>
                    <td>{formatQuantity(item.total_unresolved_quantity)}</td>
                    <td>{formatDateTime(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </DataState>
    </>
  );
}
