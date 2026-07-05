import { useRouteRunArchive } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";


function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function formatDeparture(serviceDate: string, departureTime: string) {
  return `${serviceDate} ${departureTime.slice(0, 5)}`;
}

function formatCloseResult(result: "on_time" | "late" | "unknown") {
  if (result === "on_time") {
    return "On time";
  }
  if (result === "late") {
    return "Late";
  }
  return "-";
}

export function RouteArchivePage() {
  const archive = useRouteRunArchive();
  const rows = archive.data?.results ?? [];

  return (
    <>
      <PageHeader
        title="Archiwum tras"
        description="Closed route runs with readiness, document printing and close timestamps."
      />

      <DataState isLoading={archive.isLoading} isError={archive.isError} error={archive.error}>
        {rows.length === 0 ? (
          <div className="state-box">No closed route runs found.</div>
        ) : (
          <section className="panel">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Route</th>
                    <th>Run</th>
                    <th>Branch</th>
                    <th>Planned departure</th>
                    <th>Ready at</th>
                    <th>Documents printed</th>
                    <th>Closed at</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <strong>{run.route_code}</strong>
                        <br />
                        {run.route_name}
                      </td>
                      <td>{run.run_number}</td>
                      <td>{run.branch_code}</td>
                      <td>{formatDeparture(run.service_date, run.departure_time)}</td>
                      <td>{formatDateTime(run.ready_at)}</td>
                      <td>{formatDateTime(run.documents_printed_at)}</td>
                      <td>{formatDateTime(run.closed_at)}</td>
                      <td>{formatCloseResult(run.close_result)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </DataState>
    </>
  );
}
