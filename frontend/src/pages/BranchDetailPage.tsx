import { Link, useParams } from "react-router-dom";

import { useBranch, useLocationList } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Location } from "../types/api";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function BranchDetailPage() {
  const { id } = useParams();
  const branch = useBranch(id);
  const branchCode = branch.data?.code;
  const locations = useLocationList({ branch: branchCode });

  return (
    <>
      <PageHeader
        title={branch.data ? `${branch.data.code} / ${branch.data.name}` : "Branch detail"}
        description="Warehouse branch master data and related locations."
        action={<Link className="status-pill" to="/wms/branches">Back to Branches</Link>}
      />

      <DataState isLoading={branch.isLoading} isError={branch.isError} error={branch.error}>
        {branch.data && (
          <>
            <section className="detail-grid">
              <article className="detail-card">
                <span>Branch code</span>
                <strong className="mono">{branch.data.code}</strong>
              </article>
              <article className="detail-card">
                <span>Status</span>
                <strong><StatusBadge active={branch.data.is_active} /></strong>
              </article>
              <article className="detail-card">
                <span>City</span>
                <strong>{branch.data.city || "Not set"}</strong>
              </article>
              <article className="detail-card">
                <span>Country</span>
                <strong>{branch.data.country || "Not set"}</strong>
              </article>
              <article className="detail-card">
                <span>Created</span>
                <strong>{formatDateTime(branch.data.created_at)}</strong>
              </article>
              <article className="detail-card">
                <span>Updated</span>
                <strong>{formatDateTime(branch.data.updated_at)}</strong>
              </article>
            </section>

            <section className="dashboard-section">
              <div className="section-header">
                <h2>Locations</h2>
                <Link className="dashboard-section-link" to="/wms/locations">Open Locations</Link>
              </div>
              <DataState isLoading={locations.isLoading} isError={locations.isError} error={locations.error}>
                <DataTable<Location>
                  rows={locations.data?.results ?? []}
                  emptyMessage="No locations found for this branch."
                  columns={[
                    {
                      key: "code",
                      header: "Code",
                      render: (location) => (
                        <Link className="table-link mono" to={`/wms/locations/${location.id}`}>
                          {location.code}
                        </Link>
                      ),
                    },
                    { key: "name", header: "Name", render: (location) => location.name || <span className="muted">Unnamed</span> },
                    { key: "type", header: "Type", render: (location) => location.location_type.replaceAll("_", " ") },
                    { key: "active", header: "Status", render: (location) => <StatusBadge active={location.is_active} /> },
                  ]}
                />
              </DataState>
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
