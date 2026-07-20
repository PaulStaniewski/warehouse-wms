import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useActiveBranch } from "../api/ActiveBranchContext";
import { useLookupReturnDocument, useReturnDocuments } from "../api/queries";
import { DataState } from "../components/DataState";
import { PageHeader } from "../components/PageHeader";

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "-";
}

export function ReturnsPage() {
  const { activeBranchCode } = useActiveBranch();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [reference, setReference] = useState("");
  const [search, setSearch] = useState("");
  const [lookupError, setLookupError] = useState("");
  const documents = useReturnDocuments(activeBranchCode, search);
  const lookup = useLookupReturnDocument();

  useEffect(() => {
    inputRef.current?.focus();
  }, [activeBranchCode]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const externalReference = reference.trim();
    if (!activeBranchCode || !externalReference) return;
    setLookupError("");
    try {
      const document = await lookup.mutateAsync({ branch: activeBranchCode, externalReference });
      navigate(`/wms/returns/${document.id}`);
    } catch {
      setLookupError("Return document not found for the working branch.");
    }
  }

  return (
    <>
      <PageHeader
        title="Returns"
        description={`External Return Documents for working branch ${activeBranchCode || "-"}.`}
      />

      <section className="workflow-panel">
        <form className="scan-form" onSubmit={submit}>
          <label>
            <span>Enter or scan external return reference</span>
            <input
              autoComplete="off"
              onChange={(event) => setReference(event.target.value)}
              placeholder="Example ZW1103872"
              ref={inputRef}
              value={reference}
            />
          </label>
          <button disabled={!reference.trim() || lookup.isPending} type="submit">
            Open Document
          </button>
        </form>
        {lookupError && <div className="state-box state-box--error">{lookupError}</div>}
      </section>

      <section className="filter-panel">
        <label>
          <span>Filter documents</span>
          <input onChange={(event) => setSearch(event.target.value)} placeholder="Reference, customer or product" value={search} />
        </label>
      </section>

      <DataState isError={documents.isError} error={documents.error as Error | null} isLoading={documents.isLoading}>
        <section className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Reference</th>
                <th>Customer</th>
                <th>Source document</th>
                <th>Status</th>
                <th>Expected</th>
                <th>Accepted</th>
                <th>On hold</th>
                <th>Remaining</th>
                <th>Imported</th>
              </tr>
            </thead>
            <tbody>
              {documents.data?.results.map((document) => (
                <tr key={document.id}>
                  <td>
                    <Link className="table-link mono" to={`/wms/returns/${document.id}`}>
                      {document.external_reference}
                    </Link>
                  </td>
                  <td>{document.customer_name}</td>
                  <td className="mono">{document.source_sales_document_reference || "-"}</td>
                  <td><span className="status-pill">{document.status_label}</span></td>
                  <td>{document.expected_total}</td>
                  <td>{document.accepted_total}</td>
                  <td>{document.on_hold_total}</td>
                  <td>{document.remaining_total}</td>
                  <td>{formatDate(document.imported_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </DataState>
    </>
  );
}
