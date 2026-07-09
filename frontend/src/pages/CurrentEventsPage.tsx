import { DataState } from "../components/DataState";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/PageHeader";
import { useCurrentAuditLogs } from "../api/queries";
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

export function CurrentEventsPage() {
  const auditLogs = useCurrentAuditLogs();

  return (
    <>
      <PageHeader
        title="Current events"
        description="Recent WMS events from the last 30 days, newest first."
      />

      <DataState isLoading={auditLogs.isLoading} isError={auditLogs.isError} error={auditLogs.error}>
        <DataTable<AuditLog>
          rows={auditLogs.data?.results ?? []}
          emptyMessage="No current events found."
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
              render: (event) => event.actor_display || event.actor_username || <span className="muted">System</span>,
            },
          ]}
        />
      </DataState>
    </>
  );
}
