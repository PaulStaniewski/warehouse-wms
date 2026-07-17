import { Link } from "react-router-dom";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useBranches } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { Branch } from "../types/api";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function BranchesPage() {
  const { activeBranchCode } = useActiveBranch();
  const [search, setSearch] = useState("");
  const [isActive, setIsActive] = useState("");
  const [page, setPage] = useState(1);
  const branches = useBranches({ isActive, page, search });

  function resetPage() {
    if (page !== 1) {
      setPage(1);
    }
  }

  return (
    <>
      <PageHeader
        title="Branches"
        description="Read-only register of warehouse branches available to your account."
      />

      <section className="filter-panel">
        <label>
          <span>Search</span>
          <input
            onChange={(event) => {
              setSearch(event.target.value);
              resetPage();
            }}
            placeholder="Code, name, city or country"
            value={search}
          />
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

      <DataState isLoading={branches.isLoading} isError={branches.isError} error={branches.error}>
        <DataTable<Branch>
          rows={branches.data?.results ?? []}
          emptyMessage="No branches found."
          columns={[
            {
              key: "code",
              header: "Code",
              render: (branch) => (
                <Link className="table-link mono" to={`/wms/branches/${branch.id}`}>
                  {branch.code}
                </Link>
              ),
            },
            { key: "name", header: "Name", render: (branch) => branch.name },
            { key: "city", header: "City", render: (branch) => branch.city || <span className="muted">Not set</span> },
            { key: "country", header: "Country", render: (branch) => branch.country || <span className="muted">Not set</span> },
            {
              key: "active",
              header: "Status",
              render: (branch) => <StatusBadge active={branch.is_active} />,
            },
            {
              key: "context",
              header: "Context",
              render: (branch) => (branch.code === activeBranchCode ? <span className="status-pill status-pill--ok">Working branch</span> : <span className="muted">-</span>),
            },
            { key: "updated", header: "Updated", render: (branch) => formatDateTime(branch.updated_at) },
          ]}
        />
        <div className="pagination-bar">
          <span>
            {branches.data?.count ?? 0} branches
          </span>
          <div>
            <button disabled={!branches.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">
              Previous
            </button>
            <strong>Page {page}</strong>
            <button disabled={!branches.data?.next} onClick={() => setPage((value) => value + 1)} type="button">
              Next
            </button>
          </div>
        </div>
      </DataState>
    </>
  );
}
