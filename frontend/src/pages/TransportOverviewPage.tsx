import { AlertTriangle, ArrowRight, ArchiveRestore, ClipboardCheck, RefreshCw, Route, Truck } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useTransportOverview } from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";

type SummaryCard = {
  description: string;
  icon: ReactNode;
  label: string;
  to: string;
  value: number;
};

const workflowSteps = [
  ["Preparation", "Route runs with open, syncing or picking status are managed in Routes Monitor."],
  ["Ready to close", "Prepared route runs require document printing and the existing close workflow."],
  ["Inter-branch transit", "Released, in-transit and receiving transfers are tracked through Transit workflows."],
  ["Destination receiving", "Pallet receiving remains in Scanner Receiving and Inter-branch arrivals."],
  ["Discrepancy follow-up", "Unresolved discrepancies, transit investigations and reconciliations stay in their own modules."],
  ["Archive", "Closed route runs remain available through Routes Archive."],
];

function formatDateTime(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function formatDeparture(serviceDate: string, departureTime: string) {
  return `${serviceDate} ${departureTime.slice(0, 5)}`;
}

function formatStatus(value: string) {
  return value.replaceAll("_", " ");
}

function SummaryMetric({ card }: { card: SummaryCard }) {
  return (
    <Link className={card.value > 0 ? "transport-metric transport-metric--active" : "transport-metric"} to={card.to}>
      <div className="transport-metric__icon">{card.icon}</div>
      <div>
        <span>{card.label}</span>
        <strong>{card.value}</strong>
        <p>{card.description}</p>
      </div>
      <ArrowRight size={16} />
    </Link>
  );
}

export function TransportOverviewPage() {
  const { activeBranch, activeBranchCode, isLoading: branchLoading } = useActiveBranch();
  const overview = useTransportOverview(activeBranchCode);

  if (branchLoading) {
    return <div className="state-box">Loading branch context...</div>;
  }

  if (!activeBranch || !activeBranchCode) {
    return <div className="state-box state-box--error">No active branch is available for this account.</div>;
  }

  if (overview.isLoading) {
    return <div className="state-box">Loading transport operations...</div>;
  }

  if (overview.isError) {
    return (
      <div className="state-box state-box--error">
        <p>Transport overview could not be loaded.</p>
        <button className="dashboard-metric-retry" onClick={() => void overview.refetch()} type="button">
          <RefreshCw size={15} />
          Retry
        </button>
      </div>
    );
  }

  const data = overview.data;
  if (!data) {
    return <div className="state-box state-box--error">Transport overview data is unavailable.</div>;
  }

  const summaryCards: SummaryCard[] = [
    {
      description: "Open, syncing, picking and ready-to-close route runs",
      icon: <Route size={22} />,
      label: "Active routes",
      to: "/wms/routes-monitor",
      value: data.summary.active_route_runs,
    },
    {
      description: "Open, syncing and picking route runs",
      icon: <ClipboardCheck size={22} />,
      label: "Being prepared",
      to: "/wms/routes-monitor",
      value: data.summary.preparing_route_runs,
    },
    {
      description: "Prepared route runs waiting for close workflow",
      icon: <ArchiveRestore size={22} />,
      label: "Ready to close",
      to: "/wms/routes-monitor",
      value: data.summary.ready_to_close_route_runs,
    },
    {
      description: "Released, in-transit and receiving inter-branch transfers",
      icon: <Truck size={22} />,
      label: "In transit",
      to: "/wms/transit-investigations",
      value: data.summary.transfers_in_transit,
    },
    {
      description: "Pallets in transit or receiving",
      icon: <Truck size={22} />,
      label: "Pallets awaiting receipt",
      to: "/scanner/inter-branch-arrivals",
      value: data.summary.pallets_awaiting_receipt,
    },
    {
      description: "Distinct transfers with unresolved discrepancy records",
      icon: <AlertTriangle size={22} />,
      label: "Discrepancy transfers",
      to: "/wms/discrepancies",
      value: data.summary.unresolved_discrepancy_transfers,
    },
    {
      description: "Pending or active transit investigations",
      icon: <AlertTriangle size={22} />,
      label: "Transit investigations",
      to: "/wms/transit-investigations",
      value: data.summary.transit_investigations,
    },
  ];

  const hasTransportWork =
    data.summary.active_route_runs > 0 ||
    data.summary.transfers_in_transit > 0 ||
    data.summary.pallets_awaiting_receipt > 0 ||
    data.summary.unresolved_discrepancy_transfers > 0 ||
    data.summary.transit_investigations > 0;

  return (
    <>
      <PageHeader
        title="Transport Overview"
        description={`Current route and inter-branch transport operations for ${activeBranch.code} / ${activeBranch.name}.`}
        action={<StatusBadge tone={hasTransportWork ? "loading" : "ok"} label={hasTransportWork ? "Active transport" : "Clear"} />}
      />

      <section className="transport-summary-grid">
        {summaryCards.map((card) => (
          <SummaryMetric card={card} key={card.label} />
        ))}
      </section>

      {!hasTransportWork && (
        <section className="transport-empty-state">
          <Route size={28} />
          <div>
            <h2>No active transport operations require attention for the selected branch.</h2>
            <p>Closed route runs remain available in Routes Archive.</p>
          </div>
          <Link to="/wms/routes/archive">Open Routes Archive</Link>
        </section>
      )}

      <section className="transport-layout">
        <div className="transport-panel">
          <div className="section-header">
            <h2>Requires Attention</h2>
          </div>
          {data.attention_items.length === 0 ? (
            <p className="empty-panel-text">No transport attention items are currently waiting.</p>
          ) : (
            <div className="transport-attention-list">
              {data.attention_items.map((item) => (
                <Link className="transport-attention-row" key={item.key} to={item.destination}>
                  <span>{item.label}</span>
                  <strong>{item.reference}</strong>
                  <small>
                    {item.source_branch_code}
                    {item.destination_branch_code ? ` to ${item.destination_branch_code}` : ""}
                  </small>
                  <em>{formatStatus(item.status)}</em>
                  <time>{formatDateTime(item.waiting_since)}</time>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="transport-panel">
          <div className="section-header">
            <h2>Active Routes</h2>
            <Link className="dashboard-section-link" to="/wms/routes-monitor">
              Routes Monitor
            </Link>
          </div>
          {data.active_routes.length === 0 ? (
            <p className="empty-panel-text">No active route runs for this branch.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Route</th>
                    <th>Run</th>
                    <th>Status</th>
                    <th>Lines</th>
                    <th>Progress</th>
                    <th>Departure</th>
                    <th>Ready</th>
                  </tr>
                </thead>
                <tbody>
                  {data.active_routes.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <strong>{run.route_code}</strong>
                        <br />
                        {run.route_name}
                      </td>
                      <td>{run.run_number}</td>
                      <td>{formatStatus(run.status)}</td>
                      <td>
                        {run.picked_line_count}/{run.line_count}
                      </td>
                      <td>
                        <div className="transport-progress">
                          <span style={{ width: `${Math.min(run.progress_percent, 100)}%` }} />
                        </div>
                        {run.progress_percent}%
                      </td>
                      <td>{formatDeparture(run.service_date, run.departure_time)}</td>
                      <td>{formatDateTime(run.ready_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="transport-panel transport-workflow-panel">
        <div className="section-header">
          <h2>Transport Workflow</h2>
        </div>
        <div className="transport-workflow">
          {workflowSteps.map(([stage, detail]) => (
            <article key={stage}>
              <strong>{stage}</strong>
              <p>{detail}</p>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
