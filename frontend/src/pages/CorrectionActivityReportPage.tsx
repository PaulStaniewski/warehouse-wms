import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCorrectionActivityReport } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function CorrectionActivityReportPage() {
  const { activeBranchCode } = useActiveBranch();
  const [employee, setEmployee] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [correctionReference, setCorrectionReference] = useState("");
  const [customer, setCustomer] = useState("");
  const [sourceSalesDocument, setSourceSalesDocument] = useState("");
  const [product, setProduct] = useState("");
  const report = useCorrectionActivityReport({
    branch: activeBranchCode,
    correctionReference,
    customer,
    dateFrom,
    dateTo,
    employee,
    product,
    sourceSalesDocument,
  });

  return (
    <>
      <PageHeader
        title="Correction Activity Report"
        description={`Employee-attributed sales correction activity for working branch ${activeBranchCode || "-"}.`}
      />

      <section className="filter-panel">
        <label><span>Employee</span><input onChange={(event) => setEmployee(event.target.value)} value={employee} /></label>
        <label><span>Date from</span><input onChange={(event) => setDateFrom(event.target.value)} type="date" value={dateFrom} /></label>
        <label><span>Date to</span><input onChange={(event) => setDateTo(event.target.value)} type="date" value={dateTo} /></label>
        <label><span>Correction reference</span><input onChange={(event) => setCorrectionReference(event.target.value)} value={correctionReference} /></label>
        <label><span>Customer</span><input onChange={(event) => setCustomer(event.target.value)} value={customer} /></label>
        <label><span>Source sales document</span><input onChange={(event) => setSourceSalesDocument(event.target.value)} value={sourceSalesDocument} /></label>
        <label><span>Product</span><input onChange={(event) => setProduct(event.target.value)} placeholder="SKU or barcode" value={product} /></label>
      </section>

      <DataState isError={report.isError} error={report.error as Error | null} isLoading={report.isLoading}>
        {report.data && (
          <>
            <section className="summary-grid">
              <div><span>Completed corrections</span><strong>{report.data.summary.completed_corrections}</strong></div>
              <div><span>Correction lines</span><strong>{report.data.summary.correction_lines}</strong></div>
              <div><span>Total corrected quantity</span><strong>{report.data.summary.total_corrected_quantity}</strong></div>
            </section>
            <section className="table-card">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Confirmed</th>
                    <th>Employee</th>
                    <th>Correction</th>
                    <th>Customer</th>
                    <th>Source sale</th>
                    <th>Product</th>
                    <th>Quantity</th>
                    <th>Returns Area</th>
                    <th>Movement</th>
                  </tr>
                </thead>
                <tbody>
                  {report.data.results.map((row) => (
                    <tr key={row.id}>
                      <td>{formatDate(row.confirmed_at)}</td>
                      <td>{row.employee}</td>
                      <td className="mono">{row.correction_reference}</td>
                      <td>{row.customer_name}</td>
                      <td className="mono">{row.source_sales_document_reference}</td>
                      <td>{row.product_sku}<br /><span>{row.product_name}</span></td>
                      <td>{row.corrected_quantity}</td>
                      <td>{row.returns_location_code}</td>
                      <td>{row.stock_movement ? `#${row.stock_movement}` : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
