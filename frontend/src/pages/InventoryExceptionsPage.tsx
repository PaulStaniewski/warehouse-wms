import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  ListChecks,
  PackageSearch,
  RefreshCw,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useInventoryExceptionSummary } from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import type { InventoryExceptionCategory } from "../types/api";

type CategoryPresentation = {
  icon: ReactNode;
  destination: string;
};

const categoryPresentation: Record<string, CategoryPresentation> = {
  action_queue: {
    destination: "/wms/discrepancy-actions",
    icon: <ListChecks size={22} />,
  },
  cycle_count_review: {
    destination: "/wms/cycle-count-review-queue",
    icon: <ClipboardCheck size={22} />,
  },
  picking_shortages: {
    destination: "/wms/picking-shortages",
    icon: <AlertTriangle size={22} />,
  },
  replenishment: {
    destination: "/wms/replenishment-requests",
    icon: <Boxes size={22} />,
  },
  reconciliations: {
    destination: "/wms/discrepancy-reconciliations",
    icon: <ClipboardList size={22} />,
  },
  source_reviews: {
    destination: "/wms/source-discrepancy-reviews",
    icon: <PackageSearch size={22} />,
  },
  source_stock: {
    destination: "/wms/source-stock-verifications",
    icon: <Boxes size={22} />,
  },
  transfer_discrepancies: {
    destination: "/wms/discrepancies",
    icon: <ClipboardCheck size={22} />,
  },
};

const workflowGuide = [
  ["Picking problem", "Picking Shortages"],
  ["Transfer mismatch", "Discrepancies"],
  ["Source investigation", "Source Reviews"],
  ["Final accounting", "Reconciliations"],
  ["Source stock search", "Source Stock"],
  ["Customer supply gap", "Replenishment"],
  ["Count variance", "Cycle Count Review Queue"],
];

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

function formatStatusList(statuses: string[]) {
  return statuses.map((status) => status.replaceAll("_", " ")).join(", ");
}

function categoryTone(category: InventoryExceptionCategory) {
  if (category.urgent_count > 0) {
    return "inventory-exception-card inventory-exception-card--urgent";
  }
  if (category.count > 0) {
    return "inventory-exception-card inventory-exception-card--active";
  }
  return "inventory-exception-card";
}

function CategoryCard({ category }: { category: InventoryExceptionCategory }) {
  const presentation = categoryPresentation[category.key];

  if (!presentation) {
    return null;
  }

  return (
    <article className={categoryTone(category)}>
      <div className="inventory-exception-card__icon">{presentation.icon}</div>
      <div className="inventory-exception-card__body">
        <div className="inventory-exception-card__heading">
          <h3>{category.label}</h3>
          <strong>{category.count}</strong>
        </div>
        <p>{category.description}</p>
        <dl className="inventory-exception-meta">
          <div>
            <dt>Owner</dt>
            <dd>{category.owner}</dd>
          </div>
          <div>
            <dt>Statuses</dt>
            <dd>{formatStatusList(category.included_statuses)}</dd>
          </div>
          <div>
            <dt>Oldest</dt>
            <dd>{formatDateTime(category.oldest_waiting_since)}</dd>
          </div>
          <div>
            <dt>Urgent</dt>
            <dd>{category.urgent_count}</dd>
          </div>
        </dl>
      </div>
      <Link className="inventory-exception-open" to={presentation.destination}>
        Open
        <ArrowRight size={15} />
      </Link>
    </article>
  );
}

export function InventoryExceptionsPage() {
  const { activeBranch, activeBranchCode, isLoading: branchLoading } = useActiveBranch();
  const summary = useInventoryExceptionSummary(activeBranchCode);

  if (branchLoading) {
    return <div className="state-box">Loading branch context...</div>;
  }

  if (!activeBranchCode || !activeBranch) {
    return <div className="state-box state-box--error">No active branch is available for this account.</div>;
  }

  if (summary.isLoading) {
    return <div className="state-box">Loading inventory exceptions...</div>;
  }

  if (summary.isError) {
    return (
      <div className="state-box state-box--error">
        <p>Inventory exceptions could not be loaded.</p>
        <button className="dashboard-metric-retry" onClick={() => void summary.refetch()} type="button">
          <RefreshCw size={15} />
          Retry
        </button>
      </div>
    );
  }

  const data = summary.data;
  if (!data) {
    return <div className="state-box state-box--error">Inventory exception data is unavailable.</div>;
  }

  const hasExceptions = data.total_actionable > 0;

  return (
    <>
      <PageHeader
        title="Inventory Exceptions"
        description={`Operational exception overview for ${activeBranch.code} / ${activeBranch.name}.`}
        action={<StatusBadge tone={hasExceptions ? "error" : "ok"} label={hasExceptions ? "Attention required" : "Clear"} />}
      />

      <section className="summary-grid">
        <article className="summary-card">
          <span>Total actionable</span>
          <strong>{data.total_actionable}</strong>
        </article>
        <article className="summary-card">
          <span>Active categories</span>
          <strong>{data.active_categories}</strong>
        </article>
        <article className="summary-card">
          <span>Oldest waiting</span>
          <strong className="summary-card__small">{formatDateTime(data.oldest_waiting_since)}</strong>
        </article>
        <article className="summary-card">
          <span>Leader-only work</span>
          <strong>{data.leader_only_count}</strong>
        </article>
      </section>

      {!hasExceptions && (
        <section className="inventory-exceptions-empty">
          <ListChecks size={28} />
          <div>
            <h2>No inventory exceptions currently require attention for the active branch.</h2>
            <p>Use this page as the branch-level hub when shortages, discrepancies, reconciliations or count variances appear.</p>
          </div>
        </section>
      )}

      <section className="dashboard-section">
        <div className="section-header">
          <h2>Exception Categories</h2>
        </div>
        <div className="inventory-exception-grid">
          {data.categories.map((category) => (
            <CategoryCard category={category} key={category.key} />
          ))}
        </div>
      </section>

      <section className="inventory-exceptions-layout">
        <div className="inventory-exception-panel">
          <div className="section-header">
            <h2>Requires Immediate Attention</h2>
          </div>
          {data.immediate_attention.length === 0 ? (
            <p className="empty-panel-text">No high-priority exception items are currently waiting.</p>
          ) : (
            <div className="inventory-attention-list">
              {data.immediate_attention.map((item) => (
                <Link className="inventory-attention-row" key={item.key} to={item.destination}>
                  <span>{item.category_label}</span>
                  <strong>{item.reference}</strong>
                  <small>{item.reason}</small>
                  <time>{formatDateTime(item.waiting_since)}</time>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="inventory-exception-panel">
          <div className="section-header">
            <h2>Workflow Guide</h2>
          </div>
          <div className="inventory-workflow-guide">
            {workflowGuide.map(([problem, owner]) => (
              <div key={problem}>
                <span>{problem}</span>
                <strong>{owner}</strong>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
