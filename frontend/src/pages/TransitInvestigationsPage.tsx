import { Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useTransferDiscrepancyTransitInvestigations } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

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
  }).format(new Date(value));
}

export function TransitInvestigationsPage() {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const investigations = useTransferDiscrepancyTransitInvestigations(status, search);
  const rows = investigations.data?.results ?? [];

  return (
    <>
      <PageHeader title="Transit Investigations" description="Review transfer and handoff evidence for confirmed shortages." />

      <section className="event-filter-panel">
        <label>
          <span>Status</span>
          <select onChange={(event) => setStatus(event.target.value)} value={status}>
            <option value="">All statuses</option>
            <option value="pending_investigation">Pending investigation</option>
            <option value="investigating">Investigating</option>
            <option value="completed">Completed</option>
          </select>
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Investigation, reconciliation, discrepancy, pallet or transfer"
              value={search}
            />
          </div>
        </label>
      </section>

      <DataState isLoading={investigations.isLoading} isError={investigations.isError} error={investigations.error}>
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
                  <th>Route</th>
                  <th>Status</th>
                  <th>Finding</th>
                  <th>Confirmed shortage</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/wms/transit-investigations/${item.id}`}>{item.reference}</Link>
                    </td>
                    <td>{item.reconciliation_reference}</td>
                    <td>{item.discrepancy_reference}</td>
                    <td>{item.pallet_code}</td>
                    <td>{item.transfer_reference}</td>
                    <td>
                      {item.source_branch_code} to {item.destination_branch_code}
                    </td>
                    <td>{item.status_label}</td>
                    <td>{item.finding_label || "-"}</td>
                    <td>{formatQuantity(item.destination_investigation_outcome.confirmed_shortage)}</td>
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
