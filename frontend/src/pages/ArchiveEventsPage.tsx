import { useState } from "react";

import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { useArchiveAuditLogs } from "../api/queries";
import type { AuditLog } from "../types/api";


function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatAction(value: string) {
  return value.replaceAll("_", " ");
}

export function ArchiveEventsPage() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const canFetch = Boolean(dateFrom && dateTo);
  const auditLogs = useArchiveAuditLogs(undefined, { dateFrom, dateTo });

  return (
    <>
      <PageHeader
        title="Archive events"
        description="Archive lookup requires a date range so older event history is not loaded by default."
      />

      <section className="event-filter-panel">
        <label>
          <span>Date from</span>
          <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
        </label>
        <label>
          <span>Date to</span>
          <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
        </label>
      </section>

      {!canFetch ? (
        <div className="state-box">Select both dates to load archived events.</div>
      ) : (
        <DataState isLoading={auditLogs.isLoading} isError={auditLogs.isError} error={auditLogs.error}>
          <DataTable<AuditLog>
            rows={auditLogs.data?.results ?? []}
            emptyMessage="No archived events found for this date range."
            columns={[
              { key: "created", header: "Created", render: (event) => formatDateTime(event.created_at) },
              { key: "action", header: "Action", render: (event) => formatAction(event.action_type) },
              { key: "message", header: "Message", render: (event) => event.message },
              {
                key: "entity",
                header: "Related object",
                render: (event) => `${event.entity_name}${event.entity_id ? ` / ${event.entity_id}` : ""}`,
              },
              {
                key: "actor",
                header: "Actor",
                render: (event) => event.actor_username || <span className="muted">System</span>,
              },
            ]}
          />
        </DataState>
      )}
    </>
  );
}
