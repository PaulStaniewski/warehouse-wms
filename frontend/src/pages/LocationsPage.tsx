import { Link } from "react-router-dom";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useLocationList } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Location } from "../types/api";

const locationTypes = ["storage", "picking", "receiving", "shipping", "returns"];

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function LocationsPage() {
  const { activeBranchCode } = useActiveBranch();
  const [search, setSearch] = useState("");
  const [locationType, setLocationType] = useState("");
  const [isActive, setIsActive] = useState("");
  const [page, setPage] = useState(1);
  const locations = useLocationList({ branch: activeBranchCode, isActive, locationType, page, search });

  function resetPage() {
    if (page !== 1) {
      setPage(1);
    }
  }

  return (
    <>
      <PageHeader
        title="Locations"
        description={`Read-only physical location register for working branch ${activeBranchCode || "-"}.`}
      />

      <section className="filter-panel">
        <label>
          <span>Search</span>
          <input
            onChange={(event) => {
              setSearch(event.target.value);
              resetPage();
            }}
            placeholder="Location code, name or branch"
            value={search}
          />
        </label>
        <label>
          <span>Location type</span>
          <select
            onChange={(event) => {
              setLocationType(event.target.value);
              resetPage();
            }}
            value={locationType}
          >
            <option value="">All types</option>
            {locationTypes.map((type) => (
              <option key={type} value={type}>
                {type.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select
            onChange={(event) => {
              setIsActive(event.target.value);
              resetPage();
            }}
            value={isActive}
          >
            <option value="">All statuses</option>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </label>
      </section>

      <DataState isLoading={locations.isLoading} isError={locations.isError} error={locations.error}>
        <DataTable<Location>
          rows={locations.data?.results ?? []}
          emptyMessage="No locations found."
          columns={[
            { key: "branch", header: "Branch", render: (location) => location.branch_code },
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
            { key: "updated", header: "Updated", render: (location) => formatDateTime(location.updated_at) },
          ]}
        />
        <div className="pagination-bar">
          <span>{locations.data?.count ?? 0} locations</span>
          <div>
            <button disabled={!locations.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">
              Previous
            </button>
            <strong>Page {page}</strong>
            <button disabled={!locations.data?.next} onClick={() => setPage((value) => value + 1)} type="button">
              Next
            </button>
          </div>
        </div>
      </DataState>
    </>
  );
}
