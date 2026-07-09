import { useActiveBranch } from "../api/ActiveBranchContext";
import { useInventoryItems } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import type { InventoryItem } from "../types/api";


export function InventoryPage() {
  const { activeBranchCode } = useActiveBranch();
  const inventory = useInventoryItems(activeBranchCode);

  return (
    <>
      <PageHeader title="Inventory" description="Current quantities by product, branch, and location." />
      <DataState isLoading={inventory.isLoading} isError={inventory.isError} error={inventory.error}>
        <DataTable<InventoryItem>
          rows={inventory.data?.results ?? []}
          emptyMessage="No inventory items found."
          columns={[
            { key: "product", header: "Product", render: (item) => <span className="mono">{item.product_sku}</span> },
            { key: "branch", header: "Branch", render: (item) => item.branch_code },
            { key: "location", header: "Location", render: (item) => <span className="mono">{item.location_code}</span> },
            { key: "on_hand", header: "On hand", render: (item) => item.quantity_on_hand },
            { key: "reserved", header: "Reserved", render: (item) => item.quantity_reserved },
          ]}
        />
      </DataState>
    </>
  );
}
