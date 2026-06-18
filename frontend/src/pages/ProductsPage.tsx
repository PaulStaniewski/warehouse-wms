import { useProducts } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Product } from "../types/api";


export function ProductsPage() {
  const products = useProducts();

  return (
    <>
      <PageHeader title="Products" description="SKU master data from the warehouse catalog." />
      <DataState isLoading={products.isLoading} isError={products.isError} error={products.error}>
        <DataTable<Product>
          rows={products.data?.results ?? []}
          emptyMessage="No products found."
          columns={[
            { key: "sku", header: "SKU", render: (product) => <span className="mono">{product.sku}</span> },
            { key: "name", header: "Name", render: (product) => product.name },
            { key: "barcode", header: "Barcode", render: (product) => product.barcode || <span className="muted">None</span> },
            { key: "uom", header: "Unit", render: (product) => product.unit_of_measure },
            { key: "active", header: "Status", render: (product) => <StatusBadge active={product.is_active} /> },
          ]}
        />
      </DataState>
    </>
  );
}
