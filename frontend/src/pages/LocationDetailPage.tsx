import { Link, useParams } from "react-router-dom";

import { useLocation } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function LocationDetailPage() {
  const { id } = useParams();
  const location = useLocation(id);

  return (
    <>
      <PageHeader
        title={location.data ? `${location.data.branch_code} / ${location.data.code}` : "Location detail"}
        description="Read-only physical warehouse location master data."
        action={<Link className="status-pill" to="/wms/locations">Back to Locations</Link>}
      />

      <DataState isLoading={location.isLoading} isError={location.isError} error={location.error}>
        {location.data && (
          <section className="detail-grid">
            <article className="detail-card">
              <span>Branch</span>
              <strong className="mono">{location.data.branch_code}</strong>
            </article>
            <article className="detail-card">
              <span>Location code</span>
              <strong className="mono">{location.data.code}</strong>
            </article>
            <article className="detail-card">
              <span>Name</span>
              <strong>{location.data.name || "Unnamed"}</strong>
            </article>
            <article className="detail-card">
              <span>Location type</span>
              <strong>{location.data.location_type.replaceAll("_", " ")}</strong>
            </article>
            <article className="detail-card">
              <span>Status</span>
              <strong><StatusBadge active={location.data.is_active} /></strong>
            </article>
            <article className="detail-card">
              <span>Uniqueness rule</span>
              <strong>Code is unique within branch</strong>
            </article>
            <article className="detail-card">
              <span>Created</span>
              <strong>{formatDateTime(location.data.created_at)}</strong>
            </article>
            <article className="detail-card">
              <span>Updated</span>
              <strong>{formatDateTime(location.data.updated_at)}</strong>
            </article>
          </section>
        )}
      </DataState>
    </>
  );
}
