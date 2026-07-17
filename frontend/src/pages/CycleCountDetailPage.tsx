import axios from "axios";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { canManageCycleCounts } from "../api/permissions";
import {
  useApplyCycleCountAdjustment,
  useCancelCycleCount,
  useCloseCycleCount,
  useCycleCount,
  useOpenCycleCount,
  useResolveCycleCountWithoutAdjustment,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { CycleCountLine } from "../types/api";

function formatDateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

function formatError(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || "Action failed." : "Action failed.";
}

function varianceLabel(line: CycleCountLine) {
  if (line.variance_quantity === null) return "Not counted";
  if (line.variance_status === "positive") return `Surplus ${line.variance_quantity}`;
  if (line.variance_status === "negative") return `Short ${line.variance_quantity}`;
  return "No variance";
}

function decimal(value: string | null) {
  return Number.parseFloat(value ?? "0");
}

function reconciliationClass(status: CycleCountLine["reconciliation_status"]) {
  if (status === "adjustment_applied" || status === "no_variance") return "status-pill status-pill--ok";
  if (status === "no_adjustment_required") return "status-pill status-pill--loading";
  if (status === "pending_review") return "status-pill status-pill--warning";
  return "status-pill";
}

export function CycleCountDetailPage() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const { activeMembership } = useActiveBranch();
  const count = useCycleCount(id);
  const openCount = useOpenCycleCount();
  const closeCount = useCloseCycleCount();
  const cancelCount = useCancelCycleCount();
  const applyAdjustment = useApplyCycleCountAdjustment();
  const resolveWithoutAdjustment = useResolveCycleCountWithoutAdjustment();
  const [decision, setDecision] = useState<{ mode: "apply" | "resolve"; line: CycleCountLine } | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const canManage = canManageCycleCounts(activeMembership);
  const session = count.data;
  const allLines = useMemo(() => session?.locations.flatMap((location) => location.lines) ?? [], [session]);
  const pendingLines = allLines.filter((line) => line.reconciliation_status === "pending_review");
  const isDecisionPending = applyAdjustment.isPending || resolveWithoutAdjustment.isPending;

  async function runAction(action: "open" | "close" | "cancel") {
    if (!session) return;
    try {
      if (action === "open") await openCount.mutateAsync(session.id);
      if (action === "cancel") await cancelCount.mutateAsync(session.id);
      if (action === "close") {
        const confirmed = window.confirm(
          `Close ${session.reference}? Applied adjustments have already changed inventory. Closing will not create any additional stock movement.\n\nTotal lines: ${session.lines_count}\nZero variance: ${session.zero_variance_count}\nAdjustments applied: ${session.applied_adjustment_count}\nResolved without adjustment: ${session.no_adjustment_resolution_count}\nPending variance lines: ${session.pending_variance_count}\nPositive variance quantity: ${session.positive_variance_quantity}\nNegative variance quantity: ${session.negative_variance_quantity}\nMovement warnings: ${session.movement_warning_count}`,
        );
        if (!confirmed) return;
        await closeCount.mutateAsync(session.id);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["cycle-count", id] }),
        queryClient.invalidateQueries({ queryKey: ["cycle-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-cycle-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
    } catch (error) {
      window.alert(formatError(error));
    }
  }

  async function refreshAfterDecision(refreshInventory: boolean) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["cycle-count", id] }),
      queryClient.invalidateQueries({ queryKey: ["cycle-counts"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      queryClient.invalidateQueries({ queryKey: ["stock-adjustments"] }),
      ...(refreshInventory
        ? [
            queryClient.invalidateQueries({ queryKey: ["inventory-items"] }),
            queryClient.invalidateQueries({ queryKey: ["location-contents"] }),
          ]
        : []),
    ]);
  }

  async function submitDecision() {
    if (!session || !decision) return;
    try {
      if (decision.mode === "apply") {
        await applyAdjustment.mutateAsync({ sessionId: session.id, lineId: decision.line.id, note: decisionNote });
        await refreshAfterDecision(true);
      } else {
        await resolveWithoutAdjustment.mutateAsync({ sessionId: session.id, lineId: decision.line.id, note: decisionNote });
        await refreshAfterDecision(false);
      }
      setDecision(null);
      setDecisionNote("");
    } catch (error) {
      window.alert(formatError(error));
    }
  }

  function openDecision(mode: "apply" | "resolve", line: CycleCountLine) {
    setDecision({ mode, line });
    setDecisionNote("");
  }

  return (
    <>
      <PageHeader
        title={session?.reference ?? "Cycle count"}
        description="Review expected snapshot, blind count results and variances. Closing does not change inventory."
        action={<Link className="status-pill" to="/wms/cycle-counts">Back to Cycle Counts</Link>}
      />
      <DataState isLoading={count.isLoading} isError={count.isError} error={count.error}>
        {session && (
          <>
            <section className="detail-grid">
              <article className="detail-card"><span>Status</span><strong>{session.status}</strong></article>
              <article className="detail-card"><span>Branch</span><strong>{session.branch_code}</strong></article>
              <article className="detail-card"><span>Snapshot</span><strong>{formatDateTime(session.snapshot_at)}</strong></article>
              <article className="detail-card"><span>Locations</span><strong>{session.submitted_locations_count}/{session.locations_count}</strong></article>
              <article className="detail-card"><span>Lines counted</span><strong>{session.counted_lines_count}/{session.lines_count}</strong></article>
              <article className="detail-card"><span>Variance lines</span><strong>{session.variance_lines_count}</strong></article>
              <article className="detail-card"><span>Unexpected products</span><strong>{session.unexpected_lines_count}</strong></article>
              <article className="detail-card"><span>Movements after snapshot</span><strong>{session.movement_warning_count}</strong></article>
              <article className="detail-card"><span>Pending review</span><strong>{session.pending_variance_count}</strong></article>
              <article className="detail-card"><span>Adjustments applied</span><strong>{session.applied_adjustment_count}</strong></article>
              <article className="detail-card"><span>No adjustment required</span><strong>{session.no_adjustment_resolution_count}</strong></article>
            </section>

            {canManage && (
              <div className="pagination-bar">
                <span>Leader actions</span>
                <div>
                  <button disabled={session.status !== "draft" || openCount.isPending} onClick={() => void runAction("open")} type="button">Open snapshot</button>
                  <button disabled={!["draft", "open"].includes(session.status) || cancelCount.isPending} onClick={() => void runAction("cancel")} type="button">Cancel</button>
                  <button disabled={!session.can_close || closeCount.isPending} onClick={() => void runAction("close")} type="button">Close reviewed</button>
                </div>
              </div>
            )}

            <section className="table-card">
              <h2>Variance Reconciliation</h2>
              <p className="muted">
                Each non-zero variance requires a Leader decision. Applying a variance changes inventory immediately and creates immutable stock adjustment history.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Location</th>
                      <th>Expected</th>
                      <th>Counted</th>
                      <th>Variance</th>
                      <th>State</th>
                      <th>Adjustment</th>
                      <th>Decision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allLines
                      .filter((line) => line.variance_quantity !== null && line.variance_status !== "zero")
                      .map((line) => {
                        const canAct = canManage && session.status === "awaiting_review" && line.reconciliation_status === "pending_review";
                        return (
                          <tr key={line.id}>
                            <td><span className="mono">{line.product_sku}</span> / {line.product_name}</td>
                            <td className="mono">{line.location_code}</td>
                            <td>{line.expected_quantity}</td>
                            <td>{line.counted_quantity ?? "-"}</td>
                            <td>{varianceLabel(line)}</td>
                            <td>
                              <span className={reconciliationClass(line.reconciliation_status)}>{line.reconciliation_status_label}</span>
                              {line.movement_after_snapshot && <p className="muted">Movement after snapshot</p>}
                              {line.adjustment_conflict_reason && <p className="muted">{line.adjustment_conflict_reason}</p>}
                            </td>
                            <td>
                              {line.adjustment_id ? (
                                <Link className="table-link mono" to={`/wms/stock-adjustments/${line.adjustment_id}`}>
                                  {line.adjustment_reference ?? `Adjustment ${line.adjustment_id}`}
                                </Link>
                              ) : (
                                <span className="muted">-</span>
                              )}
                            </td>
                            <td>
                              {canAct ? (
                                <div className="row-actions">
                                  <button disabled={!line.can_apply_adjustment || applyAdjustment.isPending} onClick={() => openDecision("apply", line)} type="button">
                                    Apply Stock Adjustment
                                  </button>
                                  <button disabled={resolveWithoutAdjustment.isPending} onClick={() => openDecision("resolve", line)} type="button">
                                    Resolve Without Adjustment
                                  </button>
                                </div>
                              ) : (
                                <>
                                  <span>{line.reconciled_by_username ?? "-"}</span>
                                  {line.reconciled_at && <p className="muted">{formatDateTime(line.reconciled_at)}</p>}
                                  {line.resolution_note && <p className="muted">{line.resolution_note}</p>}
                                </>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    {pendingLines.length === 0 && allLines.every((line) => line.variance_status === "zero" || line.variance_quantity === null) && (
                      <tr><td colSpan={8}>No non-zero variances require reconciliation.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            {session.locations.map((location) => (
              <section className="table-card" key={location.id}>
                <h2>{location.location_code} / {location.location_name} <span className="status-pill">{location.status}</span></h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th>Expected</th>
                        <th>Counted</th>
                        <th>Variance</th>
                        <th>Reconciliation</th>
                        <th>Type</th>
                        <th>Counted by</th>
                        <th>Warning</th>
                      </tr>
                    </thead>
                    <tbody>
                      {location.lines.map((line) => (
                        <tr key={line.id}>
                          <td><span className="mono">{line.product_sku}</span> / {line.product_name}</td>
                          <td>{line.expected_quantity}</td>
                          <td>{line.counted_quantity ?? <span className="muted">Not counted</span>}</td>
                          <td>{varianceLabel(line)}</td>
                          <td><span className={reconciliationClass(line.reconciliation_status)}>{line.reconciliation_status_label}</span></td>
                          <td>{line.is_expected ? "Expected" : "Unexpected"}</td>
                          <td>{line.counted_by_username ?? "-"}</td>
                          <td>{line.movement_after_snapshot ? "Movement after snapshot" : "-"}</td>
                        </tr>
                      ))}
                      {location.lines.length === 0 && (
                        <tr><td colSpan={8}>No stock was present in the expected snapshot for this location.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}

            {decision && (
              <section aria-modal="true" className="adjustment-confirmation" role="dialog">
                <div className="adjustment-confirmation-panel">
                  <h2>{decision.mode === "apply" ? "Apply Cycle Count adjustment" : "Resolve without adjustment"}</h2>
                  <dl>
                    <div><dt>Cycle Count</dt><dd>{session.reference}</dd></div>
                    <div><dt>Product</dt><dd>{decision.line.product_sku} / {decision.line.product_name}</dd></div>
                    <div><dt>Location</dt><dd>{decision.line.location_code}</dd></div>
                    <div><dt>Expected</dt><dd>{decision.line.expected_quantity}</dd></div>
                    <div><dt>Counted</dt><dd>{decision.line.counted_quantity}</dd></div>
                    <div><dt>Variance</dt><dd>{decision.line.variance_quantity}</dd></div>
                    <div>
                      <dt>Direction</dt>
                      <dd>{decimal(decision.line.variance_quantity) > 0 ? "Increase stock" : "Decrease stock"}</dd>
                    </div>
                  </dl>
                  {decision.line.movement_after_snapshot && <p className="state-box">Inventory movement after the snapshot is flagged for this line.</p>}
                  <label className="adjustment-note-field">
                    <span>{decision.mode === "resolve" ? "Resolution note" : "Leader note"}</span>
                    <textarea
                      onChange={(event) => setDecisionNote(event.target.value)}
                      placeholder={decision.mode === "resolve" ? "Explain why inventory should not be changed." : "Optional note for the adjustment history."}
                      value={decisionNote}
                    />
                  </label>
                  <p>
                    {decision.mode === "apply"
                      ? "Confirmation immediately changes inventory and creates immutable Stock Adjustment history. Backend stock checks remain authoritative."
                      : "Inventory will not be changed. This review decision becomes immutable history."}
                  </p>
                  <div className="form-actions">
                    <button disabled={isDecisionPending} onClick={() => { setDecision(null); setDecisionNote(""); }} type="button">Cancel</button>
                    <button
                      disabled={isDecisionPending || (decision.mode === "resolve" && decisionNote.trim().length < 5)}
                      onClick={() => void submitDecision()}
                      type="button"
                    >
                      {isDecisionPending ? "Saving..." : decision.mode === "apply" ? "Apply adjustment" : "Resolve variance"}
                    </button>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </DataState>
    </>
  );
}
