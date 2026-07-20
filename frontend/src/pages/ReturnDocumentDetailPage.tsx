import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { useRecordReturnAction, useReturnDocument } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { ReturnDocumentLine } from "../types/api";

const actionLabels: Record<string, string> = {
  accept_remaining: "Accept",
  reject_remaining: "Reject",
  put_on_hold: "Put on hold",
  accept_on_hold: "Accept held quantity",
  reject_on_hold: "Reject held quantity",
};

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

function ActionDialog({
  actionType,
  line,
  onClose,
}: {
  actionType: string;
  line: ReturnDocumentLine;
  onClose: () => void;
}) {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const recordAction = useRecordReturnAction();
  const [quantity, setQuantity] = useState(actionType.includes("on_hold") ? line.on_hold_quantity : line.remaining_quantity);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const requiresNote = actionType === "put_on_hold";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (requiresNote && !note.trim()) {
      setError("A note is required when putting quantity on hold.");
      return;
    }
    if (!id) return;
    setError("");
    try {
      await recordAction.mutateAsync({
        actionType,
        clientOperationId: crypto.randomUUID(),
        documentId: Number(id),
        lineId: line.id,
        note,
        quantity,
      });
      await queryClient.invalidateQueries({ queryKey: ["return-document", id] });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not record return action.");
    }
  }

  return (
    <div className="modal-backdrop">
      <form className="modal-card" onSubmit={submit}>
        <h2>{actionLabels[actionType]}</h2>
        <p>
          {line.product_sku} / {line.product_name}
        </p>
        <label>
          <span>Quantity</span>
          <input autoFocus min="0.001" onChange={(event) => setQuantity(event.target.value)} step="0.001" type="number" value={quantity} />
        </label>
        <label>
          <span>Note</span>
          <textarea onChange={(event) => setNote(event.target.value)} placeholder={requiresNote ? "Reason required" : "Optional note"} value={note} />
        </label>
        {error && <div className="state-box state-box--error">{error}</div>}
        <div className="modal-actions">
          <button disabled={recordAction.isPending} type="submit">Confirm</button>
          <button onClick={onClose} type="button">Cancel</button>
        </div>
      </form>
    </div>
  );
}

export function ReturnDocumentDetailPage() {
  const { id } = useParams();
  const document = useReturnDocument(id);
  const [dialog, setDialog] = useState<{ actionType: string; line: ReturnDocumentLine } | null>(null);

  return (
    <>
      <PageHeader
        title="Return Document"
        description="Process accepted, rejected and on-hold return quantities."
        action={<Link className="status-pill" to="/wms/returns">Back to Returns</Link>}
      />

      <DataState isError={document.isError} error={document.error as Error | null} isLoading={document.isLoading}>
        {document.data && (
          <>
            <section className="summary-grid">
              <div><span>External reference</span><strong>{document.data.external_reference}</strong></div>
              <div><span>Source system</span><strong>{document.data.source_system}</strong></div>
              <div><span>Customer</span><strong>{document.data.customer_name}</strong></div>
              <div><span>Source document</span><strong>{document.data.source_sales_document_reference || "-"}</strong></div>
              <div><span>Branch</span><strong>{document.data.branch_code}</strong></div>
              <div><span>Status</span><strong>{document.data.status_label}</strong></div>
              <div><span>Expected</span><strong>{document.data.expected_total}</strong></div>
              <div><span>Accepted</span><strong>{document.data.accepted_total}</strong></div>
              <div><span>Rejected</span><strong>{document.data.rejected_total}</strong></div>
              <div><span>On hold</span><strong>{document.data.on_hold_total}</strong></div>
              <div><span>Remaining</span><strong>{document.data.remaining_total}</strong></div>
              <div><span>Imported</span><strong>{formatDate(document.data.imported_at)}</strong></div>
            </section>

            <section className="table-card">
              <h2>Return Lines</h2>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Expected</th>
                    <th>Accepted</th>
                    <th>Rejected</th>
                    <th>On hold</th>
                    <th>Remaining</th>
                    <th>Latest action</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {document.data.lines.map((line) => {
                    const readOnly = document.data?.status === "completed";
                    return (
                      <tr key={line.id}>
                        <td><strong>{line.product_sku}</strong><br /><span>{line.product_name}</span></td>
                        <td>{line.expected_quantity}</td>
                        <td>{line.accepted_quantity}</td>
                        <td>{line.rejected_quantity}</td>
                        <td>{line.on_hold_quantity}</td>
                        <td>{line.remaining_quantity}</td>
                        <td>{line.latest_action ? `${line.latest_action} by ${line.latest_employee || "-"}` : "-"}</td>
                        <td className="action-row">
                          {Number(line.remaining_quantity) > 0 && !readOnly && (
                            <>
                              <button onClick={() => setDialog({ actionType: "accept_remaining", line })}>Accept</button>
                              <button onClick={() => setDialog({ actionType: "reject_remaining", line })}>Reject</button>
                              <button onClick={() => setDialog({ actionType: "put_on_hold", line })}>Put on hold</button>
                            </>
                          )}
                          {Number(line.on_hold_quantity) > 0 && !readOnly && (
                            <>
                              <button onClick={() => setDialog({ actionType: "accept_on_hold", line })}>Accept held quantity</button>
                              <button onClick={() => setDialog({ actionType: "reject_on_hold", line })}>Reject held quantity</button>
                            </>
                          )}
                          {readOnly && <span className="status-pill">Read-only</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>

            <section className="table-card">
              <h2>Action Timeline</h2>
              {document.data.actions.length === 0 ? (
                <div className="state-box">No return actions recorded yet.</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Employee</th>
                      <th>Product</th>
                      <th>Action</th>
                      <th>Quantity</th>
                      <th>Note</th>
                      <th>Movement</th>
                    </tr>
                  </thead>
                  <tbody>
                    {document.data.actions.map((action) => (
                      <tr key={action.id}>
                        <td>{formatDate(action.created_at)}</td>
                        <td>{action.employee || "-"}</td>
                        <td>{action.product_sku}</td>
                        <td>{action.action_type_label}</td>
                        <td>{action.quantity}</td>
                        <td>{action.note || "-"}</td>
                        <td>{action.stock_movement_id ? `#${action.stock_movement_id}` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </>
        )}
      </DataState>

      {dialog && <ActionDialog actionType={dialog.actionType} line={dialog.line} onClose={() => setDialog(null)} />}
    </>
  );
}
