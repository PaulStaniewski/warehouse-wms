import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import {
  useAddSalesCorrectionLine,
  useConfirmSalesCorrection,
  useRemoveSalesCorrectionLine,
  useSalesCorrection,
  useSalesHistorySearch,
  useUpdateSalesCorrectionLine,
} from "../api/queries";
import { useActiveBranch } from "../api/ActiveBranchContext";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { SalesHistoryCandidate } from "../types/api";

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

export function SalesCorrectionDetailPage() {
  const { id } = useParams();
  const { activeBranchCode } = useActiveBranch();
  const queryClient = useQueryClient();
  const correction = useSalesCorrection(id);
  const [product, setProduct] = useState("");
  const [searchedProduct, setSearchedProduct] = useState("");
  const [candidateQuantity, setCandidateQuantity] = useState<Record<number, string>>({});
  const [message, setMessage] = useState("");
  const salesHistory = useSalesHistorySearch(activeBranchCode, searchedProduct);
  const addLine = useAddSalesCorrectionLine();
  const updateLine = useUpdateSalesCorrectionLine();
  const removeLine = useRemoveSalesCorrectionLine();
  const confirm = useConfirmSalesCorrection();
  const draft = correction.data?.status === "draft";

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["sales-correction", id] });
    await queryClient.invalidateQueries({ queryKey: ["sales-history", activeBranchCode, searchedProduct] });
  }

  async function addCandidate(candidate: SalesHistoryCandidate) {
    if (!correction.data) return;
    setMessage("");
    try {
      await addLine.mutateAsync({
        correctionId: correction.data.id,
        quantity: candidateQuantity[candidate.order_line] || "1",
        sourceOrderLine: candidate.order_line,
      });
      await refresh();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not add sales line.");
    }
  }

  async function confirmCorrection() {
    if (!correction.data) return;
    setMessage("");
    try {
      await confirm.mutateAsync({ correctionId: correction.data.id, clientOperationId: crypto.randomUUID() });
      await refresh();
      setMessage("Sales correction confirmed.");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not confirm sales correction.");
    }
  }

  return (
    <>
      <PageHeader
        title="Sales Correction"
        description="Search completed sales, add selected rows to the correction draft, then confirm returned quantities."
        action={<Link className="status-pill" to="/wms/sales-corrections">Back to Sales Corrections</Link>}
      />

      <DataState isError={correction.isError} error={correction.error as Error | null} isLoading={correction.isLoading}>
        {correction.data && (
          <>
            <section className="summary-grid">
              <div><span>Reference</span><strong>{correction.data.reference}</strong></div>
              <div><span>Branch</span><strong>{correction.data.branch_code}</strong></div>
              <div><span>Status</span><strong>{correction.data.status_label}</strong></div>
              <div><span>Created by</span><strong>{correction.data.created_by_username || "-"}</strong></div>
              <div><span>Confirmed by</span><strong>{correction.data.confirmed_by_username || "-"}</strong></div>
              <div><span>Confirmed</span><strong>{formatDate(correction.data.confirmed_at)}</strong></div>
              <div><span>Lines</span><strong>{correction.data.line_count}</strong></div>
              <div><span>Total returned</span><strong>{correction.data.total_corrected_quantity}</strong></div>
            </section>

            {draft && (
              <section className="workflow-panel">
                <label>
                  <span>Product barcode or SKU</span>
                  <input
                    autoComplete="off"
                    onChange={(event) => setProduct(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") setSearchedProduct(product.trim());
                    }}
                    placeholder="Scan or enter product"
                    value={product}
                  />
                </label>
                <button disabled={!product.trim()} onClick={() => setSearchedProduct(product.trim())}>Search Sales</button>
              </section>
            )}

            {message && <div className={message.includes("confirmed") ? "state-box" : "state-box state-box--error"}>{message}</div>}

            {draft && searchedProduct && (
              <section className="table-card">
                <h2>Eligible Historical Sales</h2>
                <DataState isError={salesHistory.isError} error={salesHistory.error as Error | null} isLoading={salesHistory.isLoading}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Customer</th>
                        <th>Sales document</th>
                        <th>Product</th>
                        <th>Sold</th>
                        <th>Already corrected</th>
                        <th>Remaining</th>
                        <th>Quantity</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {salesHistory.data?.map((candidate) => (
                        <tr key={candidate.order_line} onDoubleClick={() => addCandidate(candidate)}>
                          <td>{candidate.customer_name}</td>
                          <td className="mono">{candidate.source_sales_document_reference}</td>
                          <td>{candidate.product_sku}<br /><span>{candidate.product_name}</span></td>
                          <td>{candidate.sold_quantity}</td>
                          <td>{candidate.previously_corrected_quantity}</td>
                          <td>{candidate.remaining_correctable_quantity}</td>
                          <td>
                            <input
                              min="0.001"
                              onChange={(event) => setCandidateQuantity({ ...candidateQuantity, [candidate.order_line]: event.target.value })}
                              step="0.001"
                              type="number"
                              value={candidateQuantity[candidate.order_line] || "1"}
                            />
                          </td>
                          <td><button onClick={() => addCandidate(candidate)}>Add</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </DataState>
              </section>
            )}

            <section className="table-card">
              <h2>Correction Draft Lines</h2>
              {correction.data.lines.length === 0 ? (
                <div className="state-box">No sales lines selected yet.</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Customer</th>
                      <th>Source sale</th>
                      <th>Product</th>
                      <th>Sold</th>
                      <th>Remaining</th>
                      <th>Returned quantity</th>
                      <th>Returns Area</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {correction.data.lines.map((line) => (
                      <tr key={line.id}>
                        <td>{line.customer_name_snapshot}</td>
                        <td className="mono">{line.source_sales_document_reference}</td>
                        <td>{line.product_sku}<br /><span>{line.product_name}</span></td>
                        <td>{line.sold_quantity_snapshot}</td>
                        <td>{line.remaining_correctable_quantity}</td>
                        <td>
                          {draft ? (
                            <input
                              min="0.001"
                              onBlur={(event) => updateLine.mutateAsync({ correctionId: correction.data.id, lineId: line.id, quantity: event.target.value }).then(refresh)}
                              step="0.001"
                              type="number"
                              defaultValue={line.corrected_quantity}
                            />
                          ) : (
                            line.corrected_quantity
                          )}
                        </td>
                        <td>{line.returns_location_code || "-"}</td>
                        <td>
                          {draft ? (
                            <button onClick={() => removeLine.mutateAsync({ correctionId: correction.data.id, lineId: line.id }).then(refresh)}>Remove</button>
                          ) : (
                            <span className="status-pill">Posted</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {draft && (
                <div className="action-row">
                  <button disabled={correction.data.lines.length === 0 || confirm.isPending} onClick={confirmCorrection}>
                    Confirm Sales Correction
                  </button>
                </div>
              )}
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
