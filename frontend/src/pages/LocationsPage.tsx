import { useActiveBranch } from "../api/ActiveBranchContext";
import { useLocations } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Location } from "../types/api";


export function LocationsPage() {
  const { activeBranchCode } = useActiveBranch();
  const locations = useLocations(activeBranchCode);

  return (
    <>
      <PageHeader title="Locations" description="Warehouse branches and physical storage locations." />
      <DataState isLoading={locations.isLoading} isError={locations.isError} error={locations.error}>
        <DataTable<Location>
          rows={locations.data?.results ?? []}
          emptyMessage="No locations found."
          columns={[
            { key: "branch", header: "Branch", render: (location) => location.branch_code },
            { key: "code", header: "Code", render: (location) => <span className="mono">{location.code}</span> },
            { key: "name", header: "Name", render: (location) => location.name || <span className="muted">Unnamed</span> },
            { key: "type", header: "Type", render: (location) => location.location_type },
            { key: "active", header: "Status", render: (location) => <StatusBadge active={location.is_active} /> },
          ]}
        />
      </DataState>
    </>
  );
}
