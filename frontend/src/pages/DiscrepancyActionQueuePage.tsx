import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useTransferDiscrepancyActions } from "../api/queries";
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

function actionGroup(actionType: string) {
  if (actionType === "review_destination_shortage") {
    return "destination";
  }
  if (actionType.includes("transit")) {
    return "transit";
  }
  if (actionType === "record_final_reconciliation_outcome") {
    return "final";
  }
  return "source";
}

export function DiscrepancyActionQueuePage() {
  const [actionType, setActionType] = useState("");
  const [branch, setBranch] = useState("");
  const [search, setSearch] = useState("");
  const actions = useTransferDiscrepancyActions(actionType, branch, search);
  const rows = actions.data?.results ?? [];
  const counters = useMemo(
    () => ({
      destination: rows.filter((row) => actionGroup(row.action_type) === "destination").length,
      source: rows.filter((row) => actionGroup(row.action_type) === "source").length,
      transit: rows.filter((row) => actionGroup(row.action_type) === "transit").length,
      final: rows.filter((row) => actionGroup(row.action_type) === "final").length,
    }),
    [rows],
  );

  return (
    <>
      <PageHeader
        title="Discrepancy Action Queue"
        description="Transfer discrepancy cases that currently require operational action."
      />

      <section className="summary-grid">
        <article className="summary-card">
          <span>Destination</span>
          <strong>{counters.destination}</strong>
        </article>
        <article className="summary-card">
          <span>Source</span>
          <strong>{counters.source}</strong>
        </article>
        <article className="summary-card">
          <span>Transit</span>
          <strong>{counters.transit}</strong>
        </article>
        <article className="summary-card">
          <span>Final decision</span>
          <strong>{counters.final}</strong>
        </article>
      </section>

      <section className="event-filter-panel">
        <label>
          <span>Action type</span>
          <select onChange={(event) => setActionType(event.target.value)} value={actionType}>
            <option value="">All actions</option>
            <option value="review_destination_shortage">Review destination shortage</option>
            <option value="begin_source_review">Begin source review</option>
            <option value="complete_source_review">Complete source review</option>
            <option value="acknowledge_reconciliation">Acknowledge reconciliation</option>
            <option value="begin_source_stock_verification">Begin source stock verification</option>
            <option value="continue_source_stock_verification">Continue source stock verification</option>
            <option value="complete_source_search">Complete source search</option>
            <option value="begin_transit_investigation">Begin transit investigation</option>
            <option value="complete_transit_investigation">Complete transit investigation</option>
            <option value="record_final_reconciliation_outcome">Record final reconciliation outcome</option>
          </select>
        </label>
        <label>
          <span>Branch</span>
          <input onChange={(event) => setBranch(event.target.value)} placeholder="GDA or GDY" value={branch} />
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Discrepancy, case, transfer or pallet"
              value={search}
            />
          </div>
        </label>
      </section>

      <DataState isLoading={actions.isLoading} isError={actions.isError} error={actions.error}>
        <section className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Case</th>
                  <th>Discrepancy</th>
                  <th>Transfer</th>
                  <th>Pallet</th>
                  <th>Route</th>
                  <th>Shortage</th>
                  <th>Waiting since</th>
                  <th>Open</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={9}>No discrepancy actions require work.</td>
                  </tr>
                ) : (
                  rows.map((item) => (
                    <tr key={`${item.discrepancy_reference}-${item.action_type}`}>
                      <td>
                        <strong>{item.action_label}</strong>
                        <br />
                        <span className="muted">{item.current_status_label}</span>
                      </td>
                      <td>{item.target_reference}</td>
                      <td>{item.discrepancy_reference}</td>
                      <td>{item.transfer_reference}</td>
                      <td>{item.pallet_reference}</td>
                      <td>{item.route_label || `${item.source_branch} to ${item.destination_branch}`}</td>
                      <td>{formatQuantity(item.confirmed_shortage_quantity)}</td>
                      <td>{formatDateTime(item.waiting_since)}</td>
                      <td>
                        <Link to={item.target_url}>Open</Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </DataState>
    </>
  );
}
