import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useHealth, useInventoryItems, useLocations, useOrders, useProducts } from "../api/queries";


function SummaryCard({ label, value }: { label: string; value: number | string }) {
  return (
    <article className="summary-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export function DashboardPage() {
  const health = useHealth();
  const products = useProducts();
  const inventory = useInventoryItems();
  const orders = useOrders();
  const locations = useLocations();

  const backendTone = health.isLoading ? "loading" : health.data?.status === "ok" ? "ok" : "error";
  const backendLabel = health.isLoading ? "Backend: checking" : `Backend: ${health.data?.status ?? "error"}`;

  return (
    <>
      <PageHeader
        title="Warehouse overview"
        description="Live read-only snapshot from the warehouse API."
        action={<StatusBadge tone={backendTone} label={backendLabel} />}
      />

      <DataState
        isLoading={products.isLoading || inventory.isLoading || orders.isLoading || locations.isLoading}
        isError={products.isError || inventory.isError || orders.isError || locations.isError}
        error={
          products.error || inventory.error || orders.error || locations.error || null
        }
      >
        <section className="summary-grid">
          <SummaryCard label="Products" value={products.data?.count ?? 0} />
          <SummaryCard label="Inventory items" value={inventory.data?.count ?? 0} />
          <SummaryCard label="Orders" value={orders.data?.count ?? 0} />
          <SummaryCard label="Locations" value={locations.data?.count ?? 0} />
        </section>
      </DataState>
    </>
  );
}
