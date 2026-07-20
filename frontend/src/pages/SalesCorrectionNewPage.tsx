import { useNavigate } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useCreateSalesCorrection } from "../api/queries";
import { PageHeader } from "../components/PageHeader";

export function SalesCorrectionNewPage() {
  const { activeBranchCode } = useActiveBranch();
  const navigate = useNavigate();
  const createCorrection = useCreateSalesCorrection();

  async function createDraft() {
    if (!activeBranchCode) return;
    const correction = await createCorrection.mutateAsync({ branch: activeBranchCode });
    navigate(`/wms/sales-corrections/${correction.id}`);
  }

  return (
    <>
      <PageHeader
        title="New Sales Correction"
        description="Create a draft, search completed sales by product, then confirm returned quantities into the Returns Area."
      />
      <section className="workflow-panel">
        <p>Working branch: <strong>{activeBranchCode || "-"}</strong></p>
        <button disabled={!activeBranchCode || createCorrection.isPending} onClick={createDraft}>
          Create Draft
        </button>
      </section>
    </>
  );
}
