import axios from "axios";
import { type FormEvent, useMemo, useState } from "react";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import {
  useScannerCycleCount,
  useScannerCycleCountSaveLine,
  useScannerCycleCountSubmitLocation,
} from "../api/queries";

function errorMessage(error: unknown) {
  if (!axios.isAxiosError(error)) return "Action failed.";
  return error.response?.data?.detail || Object.values(error.response?.data ?? {}).flat().join(" ") || "Action failed.";
}

export function ScannerCycleCountDetailPage() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const count = useScannerCycleCount(id);
  const saveLine = useScannerCycleCountSaveLine();
  const submitLocation = useScannerCycleCountSubmitLocation();
  const [selectedLocationId, setSelectedLocationId] = useState<number | null>(null);
  const [productCode, setProductCode] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const sessionId = Number(id);
  const selectedLocation = useMemo(
    () => count.data?.locations.find((location) => location.location === selectedLocationId) ?? null,
    [count.data, selectedLocationId],
  );

  async function saveCount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedLocation) return;
    setMessage(null);
    try {
      await saveLine.mutateAsync({ locationId: selectedLocation.location, productCode, quantity, sessionId });
      await queryClient.invalidateQueries({ queryKey: ["scanner-cycle-count", id] });
      setMessage({ type: "success", text: "Count saved." });
      setProductCode("");
      setQuantity("1");
    } catch (error) {
      setMessage({ type: "error", text: errorMessage(error) });
    }
  }

  async function submitSelected(confirmZeroes = false) {
    if (!selectedLocation) return;
    setMessage(null);
    try {
      await submitLocation.mutateAsync({ confirmZeroes, locationId: selectedLocation.location, sessionId });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-cycle-count", id] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-cycle-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["cycle-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["cycle-count", id] }),
      ]);
      setMessage({ type: "success", text: "Location submitted." });
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        const confirmed = window.confirm(`${error.response.data?.uncounted_expected_count ?? "Some"} expected products were not counted. Record them as zero and submit?`);
        if (confirmed) await submitSelected(true);
        return;
      }
      setMessage({ type: "error", text: errorMessage(error) });
    }
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner/cycle-counts"><ArrowLeft size={17} />Cycle counts</Link>
      </div>
      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}
      <section className="scanner-home-header">
        <p>{count.data?.session.reference ?? "Cycle Count"}</p>
        <h1>Blind location count</h1>
      </section>
      <section className="scanner-step-list">
        <article className="scanner-step-card">
          <header><span>1</span><h2>Select location</h2>{selectedLocation && <CheckCircle2 size={24} />}</header>
          <div className="scanner-menu-grid">
            {(count.data?.locations ?? []).map((location) => (
              <button
                className={selectedLocationId === location.location ? "scanner-menu-card scanner-menu-card--active" : "scanner-menu-card"}
                disabled={location.status === "submitted"}
                key={location.id}
                onClick={() => setSelectedLocationId(location.location)}
                type="button"
              >
                <div>
                  <strong>{location.location_code}</strong>
                  <span>{location.status} / counted {location.counted_lines_count}</span>
                </div>
              </button>
            ))}
          </div>
        </article>
        {selectedLocation && (
          <>
            <article className="scanner-step-card">
              <header><span>2</span><h2>Scan product and enter quantity</h2></header>
              <form className="scanner-scan-panel" onSubmit={saveCount}>
                <label>
                  <span>Product SKU or barcode</span>
                  <input autoFocus onChange={(event) => setProductCode(event.target.value)} placeholder="Scan product" value={productCode} />
                </label>
                <label>
                  <span>Physical quantity</span>
                  <input min="0" onChange={(event) => setQuantity(event.target.value)} step="0.001" type="number" value={quantity} />
                </label>
                <button disabled={!productCode.trim() || saveLine.isPending} type="submit">Save count</button>
              </form>
            </article>
            <article className="scanner-step-card">
              <header><span>3</span><h2>Counted products</h2></header>
              {selectedLocation.lines.map((line) => (
                <div className="scanner-confirm-summary" key={line.id}>
                  <strong>{line.product_sku}</strong>
                  <span>{line.product_name}</span>
                  <strong>{line.counted_quantity}</strong>
                  {!line.is_expected && <span>Unexpected product</span>}
                </div>
              ))}
              {selectedLocation.lines.length === 0 && <p>No products counted yet.</p>}
              <button className="scanner-confirm-button" disabled={submitLocation.isPending} onClick={() => void submitSelected(false)} type="button">
                Submit location
              </button>
            </article>
          </>
        )}
      </section>
    </>
  );
}
