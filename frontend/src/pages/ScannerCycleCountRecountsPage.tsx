import { ArrowRight, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useScannerCycleCountRecounts } from "../api/queries";

export function ScannerCycleCountRecountsPage() {
  const { activeBranchCode } = useActiveBranch();
  const recounts = useScannerCycleCountRecounts(activeBranchCode);

  return (
    <>
      <section className="scanner-home-header">
        <p>Cycle Count Recounts</p>
        <h1>Recount required</h1>
      </section>
      <section className="scanner-menu-grid">
        {(recounts.data ?? []).map((recount) => (
          <Link className="scanner-menu-card" key={recount.id} to={`/scanner/cycle-count-recounts/${recount.id}`}>
            <RotateCcw size={32} />
            <div>
              <strong>{recount.session_reference} / {recount.location_code}</strong>
              <span>{recount.product_sku} / {recount.product_name}</span>
              <span>{recount.reason}</span>
            </div>
            <ArrowRight size={24} />
          </Link>
        ))}
        {recounts.isLoading && <div className="state-box">Loading recount tasks...</div>}
        {!recounts.isLoading && (recounts.data ?? []).length === 0 && <div className="state-box">No recount tasks for this branch.</div>}
      </section>
    </>
  );
}
