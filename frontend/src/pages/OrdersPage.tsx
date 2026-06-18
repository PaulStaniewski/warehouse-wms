import { useOrders } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import type { Order } from "../types/api";


export function OrdersPage() {
  const orders = useOrders();

  return (
    <>
      <PageHeader title="Orders" description="Imported ERP orders and their current warehouse status." />
      <DataState isLoading={orders.isLoading} isError={orders.isError} error={orders.error}>
        <DataTable<Order>
          rows={orders.data?.results ?? []}
          emptyMessage="No orders found."
          columns={[
            { key: "reference", header: "External reference", render: (order) => <span className="mono">{order.external_reference}</span> },
            { key: "branch", header: "Branch", render: (order) => order.branch_code },
            { key: "status", header: "Status", render: (order) => order.status },
            { key: "customer", header: "Customer", render: (order) => order.customer_name || <span className="muted">None</span> },
            { key: "date", header: "Requested date", render: (order) => order.requested_ship_date || <span className="muted">Not set</span> },
          ]}
        />
      </DataState>
    </>
  );
}
