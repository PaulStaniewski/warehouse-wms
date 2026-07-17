import axios from "axios";
import { ArrowLeft, CheckCircle2, RotateCcw } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { useScannerCycleCountRecount, useScannerCycleCountRecountSubmit } from "../api/queries";
import { ScannerStatusMessage } from "../components/scanner/ScannerUi";

function formatError(error: unknown) {
  if (!axios.isAxiosError(error)) return "Recount could not be submitted.";
  const data = error.response?.data;
  if (data?.detail) return data.detail;
  if (data && typeof data === "object") {
    return Object.values(data).flat().join(" ");
  }
  return "Recount could not be submitted.";
}

export function ScannerCycleCountRecountDetailPage() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const recount = useScannerCycleCountRecount(id);
  const submitRecount = useScannerCycleCountRecountSubmit();
  const locationRef = useRef<HTMLInputElement>(null);
  const [locationCode, setLocationCode] = useState("");
  const [productCode, setProductCode] = useState("");
  const [quantity, setQuantity] = useState("");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    locationRef.current?.focus();
  }, [id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!recount.data) return;
    try {
      await submitRecount.mutateAsync({
        recountId: recount.data.id,
        locationCode,
        productCode,
        quantity,
      });
      setMessage({ type: "success", text: "Recount submitted." });
      setLocationCode("");
      setProductCode("");
      setQuantity("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-cycle-count-recount", id] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-cycle-count-recounts"] }),
        queryClient.invalidateQueries({ queryKey: ["cycle-count", recount.data.session] }),
        queryClient.invalidateQueries({ queryKey: ["cycle-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
      ]);
    } catch (error) {
      setMessage({ type: "error", text: formatError(error) });
      window.setTimeout(() => locationRef.current?.focus(), 0);
    }
  }

  return (
    <>
      <section className="scanner-action-header">
        <Link to="/scanner/cycle-count-recounts"><ArrowLeft size={17} />Recounts</Link>
        <p>{recount.data?.reference ?? "Cycle Count Recount"}</p>
        <h1>{recount.data?.location_code ?? "Location"} / {recount.data?.product_sku ?? "Product"}</h1>
      </section>

      {recount.isLoading && <div className="state-box">Loading recount...</div>}
      {recount.isError && <div className="state-box">Recount could not be loaded.</div>}

      {recount.data && (
        <section className="scanner-panel">
          <div className="scanner-status-row">
            <RotateCcw size={28} />
            <div>
              <strong>{recount.data.product_sku}</strong>
              <span>{recount.data.product_name}</span>
            </div>
          </div>
          <div className="scanner-summary-grid">
            <article>
              <span>Location</span>
              <strong>{recount.data.location_code}</strong>
            </article>
            <article>
              <span>Status</span>
              <strong>{recount.data.status_label}</strong>
            </article>
            <article>
              <span>Cycle Count</span>
              <strong>{recount.data.session_reference}</strong>
            </article>
          </div>
          <p className="muted">{recount.data.reason}</p>

          {recount.data.status === "submitted" ? (
            <div className="state-box">
              <CheckCircle2 size={24} />
              Recount submitted. Await leader review in WMS.
            </div>
          ) : (
            <form className="scanner-form" onSubmit={submit}>
              <label>
                <span>Scan location</span>
                <input ref={locationRef} autoComplete="off" onChange={(event) => setLocationCode(event.target.value)} value={locationCode} />
              </label>
              <label>
                <span>Scan product barcode or SKU</span>
                <input autoComplete="off" onChange={(event) => setProductCode(event.target.value)} value={productCode} />
              </label>
              <label>
                <span>Physical quantity</span>
                <input inputMode="decimal" min="0" onChange={(event) => setQuantity(event.target.value)} type="number" value={quantity} />
              </label>
              {message && <ScannerStatusMessage type={message.type}>{message.text}</ScannerStatusMessage>}
              <button disabled={submitRecount.isPending || !locationCode || !productCode || quantity === ""} type="submit">
                {submitRecount.isPending ? "Submitting..." : "Submit recount"}
              </button>
            </form>
          )}
        </section>
      )}
    </>
  );
}
