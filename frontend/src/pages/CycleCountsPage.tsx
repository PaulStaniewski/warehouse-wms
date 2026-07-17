import { Link } from "react-router-dom";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { canManageCycleCounts } from "../api/permissions";
import { useCycleCounts } from "../api/queries";
import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import type { CycleCountSession } from "../types/api";

function formatDateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

export function CycleCountsPage() {
  const { activeBranchCode, activeMembership } = useActiveBranch();
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const counts = useCycleCounts({ branch: activeBranchCode, page, search, status });

  return (
    <>
      <PageHeader
        title="Cycle Counts"
        description={`Location-based physical count sessions for ${activeBranchCode || "-"}.`}
        action={
          canManageCycleCounts(activeMembership) ? (
            <Link className="status-pill status-pill--ok" to="/wms/cycle-counts/new">New Cycle Count</Link>
          ) : undefined
        }
      />
      <section className="filter-panel">
        <label>
          <span>Search</span>
          <input onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Reference, name or note" value={search} />
        </label>
        <label>
          <span>Status</span>
          <select onChange={(event) => { setStatus(event.target.value); setPage(1); }} value={status}>
            <option value="">All</option>
            <option value="draft">Draft</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="awaiting_review">Awaiting review</option>
            <option value="closed">Closed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
      </section>
      <DataState isLoading={counts.isLoading} isError={counts.isError} error={counts.error}>
        <DataTable<CycleCountSession>
          rows={counts.data?.results ?? []}
          emptyMessage="No cycle count sessions found."
          columns={[
            {
              key: "reference",
              header: "Reference",
              render: (row) => <Link className="table-link mono" to={`/wms/cycle-counts/${row.id}`}>{row.reference}</Link>,
            },
            { key: "name", header: "Name", render: (row) => row.name || <span className="muted">-</span> },
            { key: "branch", header: "Branch", render: (row) => row.branch_code },
            { key: "status", header: "Status", render: (row) => <span className="status-pill">{row.status}</span> },
            { key: "locations", header: "Locations", render: (row) => `${row.submitted_locations_count}/${row.locations_count}` },
            { key: "lines", header: "Lines", render: (row) => `${row.counted_lines_count}/${row.lines_count}` },
            { key: "variance", header: "Variance lines", render: (row) => row.variance_lines_count },
            { key: "created", header: "Created", render: (row) => formatDateTime(row.created_at) },
            { key: "opened", header: "Opened", render: (row) => formatDateTime(row.opened_at) },
            { key: "reviewed", header: "Reviewed", render: (row) => formatDateTime(row.reviewed_at) },
          ]}
        />
        <div className="pagination-bar">
          <span>{counts.data?.count ?? 0} sessions</span>
          <div>
            <button disabled={!counts.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">Previous</button>
            <strong>Page {page}</strong>
            <button disabled={!counts.data?.next} onClick={() => setPage((value) => value + 1)} type="button">Next</button>
          </div>
        </div>
      </DataState>
    </>
  );
}
