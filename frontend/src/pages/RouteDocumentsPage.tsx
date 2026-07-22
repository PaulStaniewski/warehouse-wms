import { Printer } from "lucide-react";
import { useParams } from "react-router-dom";

import { useOrderLines, useRouteRun } from "../api/queries";
import { DataState } from "../components/DataState";


function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

export function RouteDocumentsPage() {
  const { id } = useParams();
  const routeRun = useRouteRun(id);
  const orderLines = useOrderLines(id);
  const lines = orderLines.data?.results ?? [];

  return (
    <DataState
      isLoading={routeRun.isLoading || orderLines.isLoading}
      isError={routeRun.isError || orderLines.isError}
      error={routeRun.error || orderLines.error}
    >
      {routeRun.data && (
        <section className="route-document">
          <header className="route-document-header">
            <div>
              <p>Route documents</p>
              <h1>{routeRun.data.operational_identifier}</h1>
            </div>
            <button onClick={() => window.print()} type="button">
              <Printer size={18} />
              Print
            </button>
          </header>

          <dl className="route-document-meta">
            <div>
              <dt>Branch</dt>
              <dd>{routeRun.data.branch_code}</dd>
            </div>
            <div>
              <dt>Run</dt>
              <dd>{routeRun.data.run_number}</dd>
            </div>
            <div>
              <dt>Service date</dt>
              <dd>{formatDate(routeRun.data.service_date)}</dd>
            </div>
            <div>
              <dt>Departure</dt>
              <dd>{formatTime(routeRun.data.departure_time)}</dd>
            </div>
          </dl>

          <table>
            <thead>
              <tr>
                <th>Order</th>
                <th>Product</th>
                <th>SKU</th>
                <th>Ordered</th>
                <th>Picked</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.order_reference}</td>
                  <td>{line.product_name}</td>
                  <td>{line.product_sku}</td>
                  <td>{line.quantity_ordered}</td>
                  <td>{line.quantity_picked}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </DataState>
  );
}
