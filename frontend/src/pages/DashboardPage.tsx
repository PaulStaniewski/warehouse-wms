import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import {
  useHealth,
  useInventoryItems,
  useLocations,
  useOrders,
  usePickingTasks,
  useProducts,
  useReturnBatches,
  useRouteRuns,
} from "../api/queries";
import { useActiveBranch } from "../api/ActiveBranchContext";
import type { Order, ReturnBatch } from "../types/api";


const orderStatuses = ["imported", "allocated", "picking", "completed", "cancelled"];
const pickingStatuses = ["open", "assigned", "in_progress", "completed", "cancelled"];


function SummaryCard({ label, value }: { label: string; value: number | string }) {
  return (
    <article className="summary-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function countByStatus<T extends { status: string }>(items: T[], statuses: string[]) {
  return statuses
    .map((status) => ({
      status,
      count: items.filter((item) => item.status === status).length,
    }))
    .filter((item) => item.count > 0);
}

function formatStatus(status: string) {
  return status.replaceAll("_", " ");
}

function formatDate(value: string | null) {
  if (!value) {
    return <span className="muted">Not set</span>;
  }

  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(value));
}

function StatusOverview({ title, items }: { title: string; items: Array<{ status: string; count: number }> }) {
  return (
    <section className="panel overview-panel">
      <div className="section-header">
        <h2>{title}</h2>
      </div>
      {items.length === 0 ? (
        <p className="empty-panel-text">No matching statuses yet.</p>
      ) : (
        <div className="status-overview-list">
          {items.map((item) => (
            <div className="status-overview-row" key={item.status}>
              <span>{formatStatus(item.status)}</span>
              <strong>{item.count}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function DashboardPage() {
  const { activeBranchCode } = useActiveBranch();
  const health = useHealth();
  const products = useProducts();
  const inventory = useInventoryItems(activeBranchCode);
  const orders = useOrders(activeBranchCode);
  const locations = useLocations(activeBranchCode);
  const pickingTasks = usePickingTasks();
  const returnBatches = useReturnBatches();
  const routeRuns = useRouteRuns();

  const backendTone = health.isLoading ? "loading" : health.data?.status === "ok" ? "ok" : "error";
  const backendLabel = health.isLoading ? "Backend: checking" : `Backend: ${health.data?.status ?? "error"}`;
  const allQueries = [products, inventory, orders, locations, pickingTasks, returnBatches, routeRuns];

  const orderRows = orders.data?.results ?? [];
  const pickingRows = pickingTasks.data?.results ?? [];
  const returnRows = returnBatches.data?.results ?? [];
  const routeRunRows = routeRuns.data?.results ?? [];
  const orderStatusOverview = countByStatus(orderRows, orderStatuses);
  const pickingWorkload = countByStatus(pickingRows, pickingStatuses);
  const verifiedReturnBatches = returnRows.filter((batch) => batch.status === "verified");
  const recentOrders = orderRows.slice(0, 5);

  return (
    <>
      <PageHeader
        title="Warehouse overview"
        description="Live read-only snapshot from the warehouse API."
        action={<StatusBadge tone={backendTone} label={backendLabel} />}
      />

      <DataState
        isLoading={allQueries.some((query) => query.isLoading)}
        isError={allQueries.some((query) => query.isError)}
        error={allQueries.find((query) => query.error)?.error ?? null}
      >
        <section className="summary-grid">
          <SummaryCard label="Products" value={products.data?.count ?? 0} />
          <SummaryCard label="Locations" value={locations.data?.count ?? 0} />
          <SummaryCard label="Inventory items" value={inventory.data?.count ?? 0} />
          <SummaryCard label="Orders" value={orders.data?.count ?? 0} />
          <SummaryCard label="Open picking tasks" value={pickingRows.filter((task) => task.status === "open").length} />
          <SummaryCard label="Verified returns" value={verifiedReturnBatches.length} />
          <SummaryCard label="Urgent route runs" value={routeRunRows.filter((run) => run.is_urgent).length} />
        </section>

        <section className="dashboard-grid">
          <StatusOverview title="Order status overview" items={orderStatusOverview} />
          <StatusOverview title="Picking workload" items={pickingWorkload} />
        </section>

        <section className="dashboard-section">
          <div className="section-header">
            <h2>Recent orders</h2>
          </div>
          <DataTable<Order>
            rows={recentOrders}
            emptyMessage="No recent orders found."
            columns={[
              {
                key: "reference",
                header: "External reference",
                render: (order) => <span className="mono">{order.external_reference}</span>,
              },
              { key: "branch", header: "Branch", render: (order) => order.branch_code },
              {
                key: "customer",
                header: "Customer",
                render: (order) => order.customer_name || <span className="muted">None</span>,
              },
              { key: "status", header: "Status", render: (order) => formatStatus(order.status) },
              { key: "date", header: "Requested ship date", render: (order) => formatDate(order.requested_ship_date) },
            ]}
          />
        </section>

        <section className="dashboard-section">
          <div className="section-header">
            <h2>Returns waiting for put-away</h2>
          </div>
          <DataTable<ReturnBatch>
            rows={verifiedReturnBatches}
            emptyMessage="No verified return batches waiting for put-away."
            columns={[
              {
                key: "reference",
                header: "Reference",
                render: (batch) => <span className="mono">{batch.reference}</span>,
              },
              { key: "branch", header: "Branch", render: (batch) => batch.branch_code },
              { key: "status", header: "Status", render: (batch) => formatStatus(batch.status) },
              { key: "received", header: "Received at", render: (batch) => formatDate(batch.received_at) },
            ]}
          />
        </section>
      </DataState>
    </>
  );
}
