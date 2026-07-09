import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { useState } from "react";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useTransferDiscrepancySourceReviews } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatQuantity(value: string) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(new Date(value));
}

export function SourceDiscrepancyReviewsPage() {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const { activeBranchCode } = useActiveBranch();
  const reviews = useTransferDiscrepancySourceReviews(status, search, activeBranchCode);
  const rows = reviews.data?.results ?? [];

  return (
    <>
      <PageHeader title="Source Discrepancy Reviews" description="Source-branch investigation cases for confirmed shortages." />

      <section className="event-filter-panel">
        <label>
          <span>Status</span>
          <select onChange={(event) => setStatus(event.target.value)} value={status}>
            <option value="">All statuses</option>
            <option value="pending_review">Pending review</option>
            <option value="investigating">Investigating</option>
            <option value="completed">Completed</option>
          </select>
        </label>
        <label>
          <span>Search</span>
          <div className="input-with-icon">
            <Search size={16} />
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Review, discrepancy, pallet or transfer"
              value={search}
            />
          </div>
        </label>
      </section>

      <DataState isLoading={reviews.isLoading} isError={reviews.isError} error={reviews.error}>
        <section className="panel">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Discrepancy</th>
                  <th>Pallet</th>
                  <th>Transfer</th>
                  <th>Route</th>
                  <th>Status</th>
                  <th>Confirmed shortage</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((review) => (
                  <tr key={review.id}>
                    <td>
                      <Link to={`/wms/source-discrepancy-reviews/${review.id}`}>{review.reference}</Link>
                    </td>
                    <td>{review.discrepancy_reference}</td>
                    <td>{review.pallet_code}</td>
                    <td>{review.transfer_reference}</td>
                    <td>
                      {review.source_branch_code} to {review.destination_branch_code}
                    </td>
                    <td>{review.status}</td>
                    <td>{formatQuantity(review.total_confirmed_shortage_quantity)}</td>
                    <td>{formatDateTime(review.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </DataState>
    </>
  );
}
