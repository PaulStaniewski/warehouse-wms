import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  ExternalLink,
  History,
  ListChecks,
  PackageSearch,
  RefreshCw,
  Route,
  ScanLine,
  Truck,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import {
  useCurrentAuditLogs,
  useDashboardResourceCount,
  useHealth,
  useInventoryExceptionSummary,
  useTransportOverview,
} from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";

type DashboardCountQuery = {
  count: number;
  error: Error | null;
  isError: boolean;
  isLoading: boolean;
  isSuccess: boolean;
  refetch: () => Promise<unknown>;
};

type Metric = {
  countQuery: DashboardCountQuery;
  description: string;
  icon: ReactNode;
  label: string;
  to: string;
  tone?: "attention" | "neutral";
};

type QuickLink = {
  description: string;
  external?: boolean;
  icon: ReactNode;
  label: string;
  to: string;
};

function formatStatusList(statuses: string[]) {
  return statuses.map((status) => status.replaceAll("_", " ")).join(", ");
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function MetricCard({ metric }: { metric: Metric }) {
  const { countQuery } = metric;
  const isAttention = metric.tone === "attention" && countQuery.isSuccess && countQuery.count > 0;

  return (
    <Link className={isAttention ? "dashboard-metric dashboard-metric--attention" : "dashboard-metric"} to={metric.to}>
      <div className="dashboard-metric-icon">{metric.icon}</div>
      <div>
        <span>{metric.label}</span>
        {countQuery.isLoading ? (
          <strong className="dashboard-metric-loading">Loading</strong>
        ) : countQuery.isError ? (
          <strong className="dashboard-metric-error">Error</strong>
        ) : (
          <strong>{countQuery.count}</strong>
        )}
        <p>{metric.description}</p>
      </div>
      {countQuery.isError ? (
        <button
          className="dashboard-metric-retry"
          onClick={(event) => {
            event.preventDefault();
            void countQuery.refetch();
          }}
          type="button"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      ) : (
        <ArrowRight className="dashboard-metric-arrow" size={18} />
      )}
    </Link>
  );
}

function DashboardSection({ metrics, title }: { metrics: Metric[]; title: string }) {
  if (metrics.length === 0) {
    return null;
  }

  const hasLoadedAttention = metrics.some(
    (metric) => metric.tone === "attention" && metric.countQuery.isSuccess && metric.countQuery.count > 0,
  );
  const allLoaded = metrics.every((metric) => metric.countQuery.isSuccess);

  return (
    <section className="dashboard-section">
      <div className="section-header">
        <h2>{title}</h2>
        {allLoaded && title === "Requires Attention" && (
          <span className={hasLoadedAttention ? "dashboard-section-note dashboard-section-note--warning" : "dashboard-section-note"}>
            {hasLoadedAttention ? "Review required" : "No current attention items"}
          </span>
        )}
      </div>
      <div className="dashboard-operational-grid">
        {metrics.map((metric) => (
          <MetricCard key={metric.label} metric={metric} />
        ))}
      </div>
    </section>
  );
}

function QuickAccess({ links }: { links: QuickLink[] }) {
  return (
    <section className="dashboard-section">
      <div className="section-header">
        <h2>Quick Access</h2>
      </div>
      <div className="dashboard-quick-grid">
        {links.map((link) =>
          link.external ? (
            <a className="dashboard-quick-link" href={link.to} key={link.label} rel="noopener noreferrer" target="_blank">
              {link.icon}
              <span>
                <strong>{link.label}</strong>
                <small>{link.description}</small>
              </span>
              <ExternalLink size={15} />
            </a>
          ) : (
            <Link className="dashboard-quick-link" key={link.label} to={link.to}>
              {link.icon}
              <span>
                <strong>{link.label}</strong>
                <small>{link.description}</small>
              </span>
              <ArrowRight size={15} />
            </Link>
          ),
        )}
      </div>
    </section>
  );
}

function RecentActivity({ branchCode }: { branchCode: string }) {
  const events = useCurrentAuditLogs(branchCode);
  const rows = events.data?.results.slice(0, 6) ?? [];

  return (
    <section className="dashboard-section">
      <div className="section-header">
        <h2>Recent Activity</h2>
        <Link className="dashboard-section-link" to="/wms/events/current">
          Open Event Register
        </Link>
      </div>
      <div className="dashboard-activity-panel">
        {events.isLoading ? (
          <p className="empty-panel-text">Loading recent activity...</p>
        ) : events.isError ? (
          <div className="state-box state-box--error">Could not load recent activity.</div>
        ) : rows.length === 0 ? (
          <p className="empty-panel-text">No recent branch activity found.</p>
        ) : (
          rows.map((event) => (
            <Link className="dashboard-activity-row" key={event.id} to={`/wms/events/${event.source}/${event.id}`}>
              <time>{formatDateTime(event.created_at)}</time>
              <div>
                <strong>{event.event_type_label || event.event_type.replaceAll("_", " ")}</strong>
                <span>{event.message || event.reference || event.entity_name}</span>
              </div>
              <small>{event.actor_display || event.actor_username || "System"}</small>
            </Link>
          ))
        )}
      </div>
    </section>
  );
}

export function DashboardPage() {
  const { activeBranch, activeBranchCode, activeMembership, isLoading: branchLoading } = useActiveBranch();
  const health = useHealth();

  const openPickingShortages = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/picking-shortages/",
    key: "picking-shortages-open",
    statuses: ["open"],
  });
  const unresolvedDiscrepancies = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/transfer-discrepancies/",
    key: "transfer-discrepancies-unresolved",
    statuses: ["open", "investigating"],
  });
  const sourceReviews = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/transfer-discrepancy-source-reviews/",
    key: "source-reviews-active",
    statuses: ["pending_review", "investigating"],
  });
  const reconciliations = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/transfer-discrepancy-reconciliations/",
    key: "reconciliations-active",
    statuses: ["pending_action", "in_progress", "manual_action_required"],
  });
  const actionQueue = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/transfer-discrepancy-actions/",
    key: "action-queue",
  });
  const cycleCountReviewQueue = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/cycle-count-review-queue/",
    key: "cycle-count-review-queue",
  });
  const activeOrders = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/orders/",
    key: "active-orders",
    statuses: ["imported", "allocated", "picking"],
  });
  const openReplenishment = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/replenishment-requests/",
    key: "replenishment-open",
    statuses: ["pending_order"],
  });
  const activeRoutes = useDashboardResourceCount({
    branch: activeBranchCode,
    branchParam: "branch_code",
    endpoint: "/route-runs/",
    key: "active-routes",
    statuses: ["open", "syncing", "picking", "ready_to_close"],
  });
  const routesAwaitingClosure = useDashboardResourceCount({
    branch: activeBranchCode,
    branchParam: "branch_code",
    endpoint: "/route-runs/",
    key: "routes-awaiting-closure",
    statuses: ["ready_to_close"],
  });
  const transitInvestigations = useDashboardResourceCount({
    branch: activeBranchCode,
    endpoint: "/transfer-discrepancy-transit-investigations/",
    key: "transit-investigations-active",
    statuses: ["pending_investigation", "investigating"],
  });
  const inventoryExceptions = useInventoryExceptionSummary(activeBranchCode);
  const transportOverview = useTransportOverview(activeBranchCode);
  const inventoryExceptionCount: DashboardCountQuery = {
    count: inventoryExceptions.data?.total_actionable ?? 0,
    error: inventoryExceptions.error,
    isError: inventoryExceptions.isError,
    isLoading: inventoryExceptions.isLoading,
    isSuccess: inventoryExceptions.isSuccess,
    refetch: inventoryExceptions.refetch,
  };
  const transportOverviewCount: DashboardCountQuery = {
    count:
      (transportOverview.data?.summary.active_route_runs ?? 0) +
      (transportOverview.data?.summary.transfers_in_transit ?? 0) +
      (transportOverview.data?.summary.pallets_awaiting_receipt ?? 0) +
      (transportOverview.data?.summary.unresolved_discrepancy_transfers ?? 0) +
      (transportOverview.data?.summary.transit_investigations ?? 0),
    error: transportOverview.error,
    isError: transportOverview.isError,
    isLoading: transportOverview.isLoading,
    isSuccess: transportOverview.isSuccess,
    refetch: transportOverview.refetch,
  };

  const backendTone = health.isLoading ? "loading" : health.data?.status === "ok" ? "ok" : "error";
  const backendLabel = health.isLoading ? "Backend: checking" : `Backend: ${health.data?.status ?? "error"}`;

  if (branchLoading) {
    return <div className="state-box">Loading branch context...</div>;
  }

  if (!activeBranchCode || !activeBranch || !activeMembership) {
    return (
      <div className="state-box state-box--error">
        No active branch is available for this account.
      </div>
    );
  }

  const attentionMetrics: Metric[] = [
    {
      countQuery: inventoryExceptionCount,
      description: "Unified branch exception overview",
      icon: <ListChecks size={22} />,
      label: "Inventory exceptions",
      to: "/wms/inventory-exceptions",
      tone: "attention",
    },
    {
      countQuery: openPickingShortages,
      description: `Status: ${formatStatusList(["open"])}`,
      icon: <AlertTriangle size={22} />,
      label: "Open picking shortages",
      to: "/wms/picking-shortages",
      tone: "attention",
    },
    {
      countQuery: unresolvedDiscrepancies,
      description: `Statuses: ${formatStatusList(["open", "investigating"])}`,
      icon: <ClipboardCheck size={22} />,
      label: "Unresolved discrepancies",
      to: "/wms/discrepancies",
      tone: "attention",
    },
    {
      countQuery: sourceReviews,
      description: `Statuses: ${formatStatusList(["pending_review", "investigating"])}`,
      icon: <PackageSearch size={22} />,
      label: "Source reviews",
      to: "/wms/source-discrepancy-reviews",
      tone: "attention",
    },
    {
      countQuery: reconciliations,
      description: `Statuses: ${formatStatusList(["pending_action", "in_progress", "manual_action_required"])}`,
      icon: <ClipboardList size={22} />,
      label: "Reconciliations",
      to: "/wms/discrepancy-reconciliations",
      tone: "attention",
    },
  ];

  const warehouseMetrics: Metric[] = [
    {
      countQuery: actionQueue,
      description: "Currently actionable discrepancy work",
      icon: <ListChecks size={22} />,
      label: "Action queue",
      to: "/wms/discrepancy-actions",
    },
    {
      countQuery: cycleCountReviewQueue,
      description: "Cycle Count variances, recounts and close-ready sessions",
      icon: <ClipboardCheck size={22} />,
      label: "Cycle Count review",
      to: "/wms/cycle-count-review-queue",
      tone: "attention",
    },
    {
      countQuery: activeOrders,
      description: `Statuses: ${formatStatusList(["imported", "allocated", "picking"])}`,
      icon: <ClipboardList size={22} />,
      label: "Active orders",
      to: "/wms/orders",
    },
    {
      countQuery: openReplenishment,
      description: `Status: ${formatStatusList(["pending_order"])}`,
      icon: <Boxes size={22} />,
      label: "Open replenishment",
      to: "/wms/replenishment-requests",
    },
  ];

  const transportMetrics: Metric[] = [
    {
      countQuery: transportOverviewCount,
      description: "Routes, transfers, pallets and transit attention",
      icon: <Truck size={22} />,
      label: "Transport overview",
      to: "/wms/transport-overview",
      tone: "attention",
    },
    {
      countQuery: activeRoutes,
      description: `Statuses: ${formatStatusList(["open", "syncing", "picking", "ready_to_close"])}`,
      icon: <Route size={22} />,
      label: "Active route runs",
      to: "/wms/routes-monitor",
    },
    {
      countQuery: routesAwaitingClosure,
      description: `Status: ${formatStatusList(["ready_to_close"])}`,
      icon: <Truck size={22} />,
      label: "Routes awaiting closure",
      to: "/wms/routes-monitor",
      tone: "attention",
    },
    {
      countQuery: transitInvestigations,
      description: `Statuses: ${formatStatusList(["pending_investigation", "investigating"])}`,
      icon: <Truck size={22} />,
      label: "Transit investigations",
      to: "/wms/transit-investigations",
      tone: "attention",
    },
  ];

  const quickLinks: QuickLink[] = [
    { description: "Unified discrepancy work", icon: <ListChecks size={20} />, label: "Action Queue", to: "/wms/discrepancy-actions" },
    { description: "Route progress and close readiness", icon: <Route size={20} />, label: "Routes Monitor", to: "/wms/routes-monitor" },
    { description: "Branch stock positions", icon: <Boxes size={20} />, label: "Inventory", to: "/wms/inventory" },
    { description: "Open shortage follow-up", icon: <AlertTriangle size={20} />, label: "Picking Shortages", to: "/wms/picking-shortages" },
    { description: "Open handheld scanner UI", external: true, icon: <ScanLine size={20} />, label: "Open Scanner", to: "/scanner" },
  ];

  return (
    <>
      <PageHeader
        title="Warehouse overview"
        description={`Operational snapshot for ${activeBranch.code} / ${activeBranch.name}.`}
        action={<StatusBadge tone={backendTone} label={backendLabel} />}
      />

      <DashboardSection metrics={attentionMetrics} title="Requires Attention" />
      <DashboardSection metrics={warehouseMetrics} title="Warehouse Operations" />
      <DashboardSection metrics={transportMetrics} title="Transport & Routes" />
      <QuickAccess links={quickLinks} />
      <RecentActivity branchCode={activeBranchCode} />
    </>
  );
}
