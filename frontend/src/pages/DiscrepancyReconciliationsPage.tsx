import { Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useTransferDiscrepancyReconciliations } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

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
  }).format(new Date(value));
}

export function DiscrepancyReconciliationsPage() {
  const [status, setStatus] = useState("");
  const [route, setRoute] = useState("");
  const [search, setSearch] = useState("");
  const reconciliations = useTransferDiscrepancyReconciliations(status, route, search);
  const rows = reconciliations.data?.results ?? [];

  return (
    <>
      <PageHeader title="Discrepancy Reconciliations" description="Next operational action after completed source reviews." />

      <section className="event-filter-panel">
        <label>
          <span>Status</span>
          <select onChange={(event) => setStatus(event.target.value)} value={status}>
            <option value="">All statuses</option>
            <option value="pending_action">Pending action</option>
            <option value="in_progress">In progress</option>
            <option value="manual_action_required">Manual action required</option>
            <option value="completed">Completed</option>
          </select>
        </label>
        <label>
          <span>Route</span>
          <select onChange={(event) => setRoute(event.target.value)} value={route}>
            <option value="">All routes</option>
            <option value="source_stock_verification">Source stock verification</option>
            <option value="transit_investigation">Transit investigation</option>
            <option value="manual_reconciliation">Manual reconciliation</option>
          </select>
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Reconciliation, review, discrepancy, pallet or transfer"
              value={search}
            />
          </div>
        </label>
      </section>

      <DataState isLoading={reconciliations.isLoading} isError={reconciliations.isError} error={reconciliations.error}>
        <section className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Discrepancy</th>
                  <th>Source review</th>
                  <th>Pallet</th>
                  <th>Transfer</th>
                  <th>Route</th>
                  <th>Status</th>
                  <th>Final outcome</th>
                  <th>Branches</th>
                  <th>Confirmed shortage</th>
                  <th>Created</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/wms/discrepancy-reconciliations/${item.id}`}>{item.reference}</Link>
                    </td>
                    <td>{item.discrepancy_reference}</td>
                    <td>{item.source_review_reference}</td>
                    <td>{item.pallet_code}</td>
                    <td>{item.transfer_reference}</td>
                    <td>{item.route_label}</td>
                    <td>{item.status_label}</td>
                    <td>{item.manual_decision?.outcome_label ?? "-"}</td>
                    <td>
                      {item.source_branch_code} to {item.destination_branch_code}
                    </td>
                    <td>{formatQuantity(item.total_confirmed_shortage_quantity)}</td>
                    <td>{formatDateTime(item.created_at)}</td>
                    <td>{formatDateTime(item.completed_at)}</td>
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
