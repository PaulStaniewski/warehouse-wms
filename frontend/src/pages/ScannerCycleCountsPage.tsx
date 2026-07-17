import { ArrowRight, ClipboardList } from "lucide-react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useScannerCycleCounts } from "../api/queries";

export function ScannerCycleCountsPage() {
  const { activeBranchCode } = useActiveBranch();
  const counts = useScannerCycleCounts(activeBranchCode);

  return (
    <>
      <section className="scanner-home-header">
        <p>Cycle Counts</p>
        <h1>Available count sessions</h1>
      </section>
      <section className="scanner-menu-grid">
        {(counts.data?.results ?? []).map((session) => (
          <Link className="scanner-menu-card" key={session.id} to={`/scanner/cycle-counts/${session.id}`}>
            <ClipboardList size={32} />
            <div>
              <strong>{session.reference}</strong>
              <span>{session.name || session.branch_code} / {session.submitted_locations_count}/{session.locations_count} locations</span>
            </div>
            <ArrowRight size={24} />
          </Link>
        ))}
        {counts.isLoading && <div className="state-box">Loading cycle counts...</div>}
        {!counts.isLoading && (counts.data?.results ?? []).length === 0 && <div className="state-box">No executable cycle counts.</div>}
      </section>
    </>
  );
}
