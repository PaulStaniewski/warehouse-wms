import axios from "axios";
import { Search } from "lucide-react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { canManageCycleCounts } from "../api/permissions";
import {
  useAcceptCycleCountRecount,
  useApplyCycleCountAdjustment,
  useCancelCycleCountRecount,
  useCloseCycleCount,
  useCycleCountReviewQueue,
  useRequestCycleCountRecount,
  useResolveCycleCountWithoutAdjustment,
} from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";
import type { CycleCountReviewQueueItem } from "../types/api";

const itemTypes = [
  ["", "All work"],
  ["stale_variance", "Stale variance"],
  ["recount_waiting_review", "Recount waiting review"],
  ["accepted_recount_pending_reconciliation", "Accepted recount pending reconciliation"],
  ["variance_pending_review", "Pending variance"],
  ["session_waiting_close", "Session ready to close"],
  ["recount_requested", "Recount requested"],
  ["recount_in_progress", "Recount in progress"],
];

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

function formatError(error: unknown) {
  if (!axios.isAxiosError(error)) return "Action failed.";
  const data = error.response?.data;
  if (data?.detail) return data.detail;
  if (data && typeof data === "object") return Object.values(data).flat().join(" ");
  return "Action failed.";
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    accept_recount: "Accept Recount",
    apply_adjustment: "Apply Stock Adjustment",
    cancel_recount: "Cancel Recount",
    close_session: "Close Session",
    open_detail: "Open Detail",
    open_scanner_recount: "Open Scanner Recount",
    request_recount: "Request Recount",
    resolve_without_adjustment: "Resolve Without Adjustment",
  };
  return labels[action] ?? action;
}

export function CycleCountReviewQueuePage() {
  const queryClient = useQueryClient();
  const { activeBranchCode, activeMembership } = useActiveBranch();
  const canManage = canManageCycleCounts(activeMembership);
  const [itemType, setItemType] = useState("");
  const [search, setSearch] = useState("");
  const [staleOnly, setStaleOnly] = useState(false);
  const [page, setPage] = useState(1);
  const queue = useCycleCountReviewQueue({ branch: activeBranchCode, itemType, page, search, staleOnly });
  const applyAdjustment = useApplyCycleCountAdjustment();
  const resolveWithoutAdjustment = useResolveCycleCountWithoutAdjustment();
  const requestRecount = useRequestCycleCountRecount();
  const acceptRecount = useAcceptCycleCountRecount();
  const cancelRecount = useCancelCycleCountRecount();
  const closeSession = useCloseCycleCount();
  const isMutating = applyAdjustment.isPending || resolveWithoutAdjustment.isPending || requestRecount.isPending || acceptRecount.isPending || cancelRecount.isPending || closeSession.isPending;

  async function refreshAfterAction(refreshInventory = false) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["cycle-count-review-queue"] }),
      queryClient.invalidateQueries({ queryKey: ["cycle-counts"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-cycle-count-recounts"] }),
      queryClient.invalidateQueries({ queryKey: ["stock-adjustments"] }),
      ...(refreshInventory
        ? [
            queryClient.invalidateQueries({ queryKey: ["inventory-items"] }),
            queryClient.invalidateQueries({ queryKey: ["location-contents"] }),
          ]
        : []),
    ]);
  }

  async function runAction(item: CycleCountReviewQueueItem, action: string) {
    if (!canManage || isMutating) return;
    try {
      if (action === "apply_adjustment" && item.line) {
        const confirmed = window.confirm(
          `Apply stock adjustment for ${item.session_reference}?\n\nProduct: ${item.product_sku}\nLocation: ${item.location_code}\nEffective variance: ${item.effective_variance}\n\nThis immediately changes inventory and creates immutable Stock Adjustment history.`,
        );
        if (!confirmed) return;
        await applyAdjustment.mutateAsync({ sessionId: item.session, lineId: item.line });
        await refreshAfterAction(true);
      }
      if (action === "resolve_without_adjustment" && item.line) {
        const note = window.prompt("Explain why this variance requires no inventory adjustment.");
        if (!note || note.trim().length < 5) return;
        await resolveWithoutAdjustment.mutateAsync({ sessionId: item.session, lineId: item.line, note });
        await refreshAfterAction(false);
      }
      if (action === "request_recount" && item.line) {
        const reason = window.prompt("Explain why a physical recount is required.");
        if (!reason || reason.trim().length < 5) return;
        await requestRecount.mutateAsync({ sessionId: item.session, lineId: item.line, reason });
        await refreshAfterAction(false);
      }
      if (action === "accept_recount" && item.recount) {
        const confirmed = window.confirm("Accept this recount as effective evidence? This does not change inventory.");
        if (!confirmed) return;
        await acceptRecount.mutateAsync({ sessionId: item.session, recountId: item.recount });
        await refreshAfterAction(false);
      }
      if (action === "cancel_recount" && item.recount) {
        const note = window.prompt("Explain why this recount is being cancelled.");
        if (!note || note.trim().length < 5) return;
        await cancelRecount.mutateAsync({ sessionId: item.session, recountId: item.recount, note });
        await refreshAfterAction(false);
      }
      if (action === "close_session") {
        const confirmed = window.confirm(`Close ${item.session_reference}? Closing does not create additional stock movement.`);
        if (!confirmed) return;
        await closeSession.mutateAsync(item.session);
        await refreshAfterAction(false);
      }
    } catch (error) {
      window.alert(formatError(error));
    }
  }

  const summary = queue.data?.summary;

  return (
    <>
      <PageHeader
        title="Cycle Count Review Queue"
        description="Branch Cycle Count variances, recounts and close-ready sessions that require Leader attention."
      />

      <section className="summary-grid">
        <article className="summary-card"><span>Total</span><strong>{summary?.total ?? "-"}</strong></article>
        <article className="summary-card"><span>Stale variance</span><strong>{summary?.stale_variance ?? "-"}</strong></article>
        <article className="summary-card"><span>Pending variance</span><strong>{summary?.variance_pending_review ?? "-"}</strong></article>
        <article className="summary-card"><span>Recount review</span><strong>{summary?.recount_waiting_review ?? "-"}</strong></article>
        <article className="summary-card"><span>Accepted recount</span><strong>{summary?.accepted_recount_pending_reconciliation ?? "-"}</strong></article>
        <article className="summary-card"><span>Ready to close</span><strong>{summary?.session_waiting_close ?? "-"}</strong></article>
      </section>

      <section className="event-filter-panel">
        <label>
          <span>Work type</span>
          <select onChange={(event) => { setItemType(event.target.value); setPage(1); }} value={itemType}>
            {itemTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
              placeholder="Cycle Count, location, SKU, recount"
              value={search}
            />
          </div>
        </label>
        <label className="checkbox-row">
          <input checked={staleOnly} onChange={(event) => { setStaleOnly(event.target.checked); setPage(1); }} type="checkbox" />
          <span>Stale/conflict only</span>
        </label>
      </section>

      {!canManage && <div className="state-box state-box--error">This queue requires Leader access for the active branch.</div>}

      <DataState isLoading={queue.isLoading} isError={queue.isError} error={queue.error}>
        <section className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Work</th>
                  <th>Cycle Count</th>
                  <th>Location</th>
                  <th>Product</th>
                  <th>Quantities</th>
                  <th>Warning</th>
                  <th>Waiting since</th>
                  <th>Next action</th>
                </tr>
              </thead>
              <tbody>
                {(queue.data?.results ?? []).length === 0 ? (
                  <tr><td colSpan={8}>No Cycle Count review items require attention for the active branch.</td></tr>
                ) : (
                  queue.data?.results.map((item) => (
                    <tr key={item.key}>
                      <td>
                        <strong>{item.item_type_label}</strong>
                        {item.recount_reference && <p className="muted mono">{item.recount_reference}</p>}
                      </td>
                      <td><Link className="table-link mono" to={item.detail_url}>{item.session_reference}</Link></td>
                      <td className="mono">{item.location_code || "-"}</td>
                      <td>{item.product_sku ? <><span className="mono">{item.product_sku}</span> / {item.product_name}</> : "-"}</td>
                      <td>
                        {item.line ? (
                          <>
                            Expected {item.expected_quantity}<br />
                            Effective {item.effective_counted_quantity || "-"}<br />
                            Variance {item.effective_variance || "-"}
                          </>
                        ) : (
                          "Session ready"
                        )}
                      </td>
                      <td>
                        {item.is_stale ? "Stale or conflicted" : "-"}
                        {item.movement_after_snapshot && <p className="muted">Movement after snapshot</p>}
                        {item.movement_after_baseline && <p className="muted">Movement after recount baseline</p>}
                      </td>
                      <td>{formatDateTime(item.waiting_since)}</td>
                      <td>
                        <div className="row-actions">
                          {item.valid_actions.map((action) => (
                            action === "open_detail" ? (
                              <Link className="status-pill" key={action} to={item.detail_url}>{actionLabel(action)}</Link>
                            ) : action === "open_scanner_recount" && item.recount ? (
                              <Link className="status-pill" key={action} to={`/scanner/cycle-count-recounts/${item.recount}`}>{actionLabel(action)}</Link>
                            ) : (
                              <button disabled={!canManage || isMutating} key={action} onClick={() => void runAction(item, action)} type="button">
                                {actionLabel(action)}
                              </button>
                            )
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
        <div className="pagination-bar">
          <span>{queue.data?.count ?? 0} items</span>
          <div>
            <button disabled={!queue.data?.previous || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">Previous</button>
            <button disabled={!queue.data?.next} onClick={() => setPage((value) => value + 1)} type="button">Next</button>
          </div>
        </div>
      </DataState>
    </>
  );
}
