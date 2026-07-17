import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";

import {
  useScannerLocationContents,
  useScannerProductLookup,
  useScannerQuickTransfer,
} from "../api/queries";


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function normalize(value: string | null | undefined) {
  return (value ?? "").toLowerCase();
}

export function ScannerQuickTransferPage() {
  const queryClient = useQueryClient();
  const [sourceInput, setSourceInput] = useState("");
  const [sourceCode, setSourceCode] = useState("");
  const [productInput, setProductInput] = useState("");
  const [productCode, setProductCode] = useState("");
  const [targetInput, setTargetInput] = useState("");
  const [targetCode, setTargetCode] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const sourceLookup = useScannerLocationContents(sourceCode);
  const productLookup = useScannerProductLookup(productCode);
  const targetLookup = useScannerLocationContents(targetCode);
  const transfer = useScannerQuickTransfer();
  const productInSource = sourceLookup.data?.inventory_items.find((item) => {
    const scanned = normalize(productCode);
    return normalize(item.product_sku) === scanned || normalize(item.product_barcode) === scanned;
  });
  const canConfirm = Boolean(sourceLookup.data && productLookup.data && targetLookup.data && productInSource);

  function submitSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setSourceCode(sourceInput.trim());
    setProductCode("");
    setTargetCode("");
  }

  function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setProductCode(productInput.trim());
    setTargetCode("");
  }

  function submitTarget(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setTargetCode(targetInput.trim());
  }

  async function confirmTransfer() {
    setMessage(null);

    try {
      const result = await transfer.mutateAsync({
        productCode,
        quantity,
        sourceLocationCode: sourceCode,
        targetLocationCode: targetCode,
      });
      setMessage({ type: "success", text: result.message });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scanner-location-contents", sourceCode] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-location-contents", targetCode] }),
        queryClient.invalidateQueries({ queryKey: ["scanner-product-lookup", productCode] }),
        queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory-items"] }),
        queryClient.invalidateQueries({ queryKey: ["stock-transfers"] }),
      ]);
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Quick transfer failed.") });
    }
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
      </div>

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <section className="scanner-tool-panel">
        <div>
          <p>Quick transfer</p>
          <h1>Move stock</h1>
        </div>
      </section>

      <section className="scanner-step-list">
        <article className="scanner-step-card">
          <header>
            <span>1</span>
            <h2>Scan source location</h2>
            {sourceLookup.data && <CheckCircle2 size={24} />}
          </header>
          <form className="scanner-scan-panel" onSubmit={submitSource}>
            <label htmlFor="source-location">
              <span>Source location</span>
              <input
                autoComplete="off"
                autoFocus
                id="source-location"
                onChange={(event) => setSourceInput(event.target.value)}
                placeholder="Example A-01-01"
                value={sourceInput}
              />
            </label>
            <button disabled={!sourceInput.trim() || sourceLookup.isFetching} type="submit">
              Confirm
            </button>
          </form>
          {sourceLookup.isError && (
            <p className="scanner-inline-error">{getErrorMessage(sourceLookup.error, "Source location not found.")}</p>
          )}
          {sourceLookup.data && <p className="scanner-inline-ok">Source: {sourceLookup.data.location.code}</p>}
        </article>

        <article className="scanner-step-card">
          <header>
            <span>2</span>
            <h2>Scan product</h2>
            {productInSource && <CheckCircle2 size={24} />}
          </header>
          <form className="scanner-scan-panel" onSubmit={submitProduct}>
            <label htmlFor="transfer-product">
              <span>Product SKU or barcode</span>
              <input
                autoComplete="off"
                disabled={!sourceLookup.data}
                id="transfer-product"
                onChange={(event) => setProductInput(event.target.value)}
                placeholder="Example FILTR-001"
                value={productInput}
              />
            </label>
            <button disabled={!sourceLookup.data || !productInput.trim() || productLookup.isFetching} type="submit">
              Confirm
            </button>
          </form>
          {productLookup.isError && (
            <p className="scanner-inline-error">{getErrorMessage(productLookup.error, "Product not found.")}</p>
          )}
          {productLookup.data && !productInSource && (
            <p className="scanner-inline-error">Product is not available on the source location.</p>
          )}
          {productInSource && (
            <p className="scanner-inline-ok">
              Product: {productInSource.product_sku} / source quantity {productInSource.quantity_on_hand}
            </p>
          )}
        </article>

        <article className="scanner-step-card">
          <header>
            <span>3</span>
            <h2>Scan target location</h2>
            {targetLookup.data && <CheckCircle2 size={24} />}
          </header>
          <form className="scanner-scan-panel" onSubmit={submitTarget}>
            <label htmlFor="target-location">
              <span>Target location</span>
              <input
                autoComplete="off"
                disabled={!productInSource}
                id="target-location"
                onChange={(event) => setTargetInput(event.target.value)}
                placeholder="Example A-02-01"
                value={targetInput}
              />
            </label>
            <button disabled={!productInSource || !targetInput.trim() || targetLookup.isFetching} type="submit">
              Confirm
            </button>
          </form>
          {targetLookup.isError && (
            <p className="scanner-inline-error">{getErrorMessage(targetLookup.error, "Target location not found.")}</p>
          )}
          {targetLookup.data && <p className="scanner-inline-ok">Target: {targetLookup.data.location.code}</p>}
        </article>

        <article className="scanner-step-card">
          <header>
            <span>4</span>
            <h2>Confirm transfer</h2>
          </header>
          <label className="scanner-quantity-field" htmlFor="transfer-quantity">
            <span>Quantity</span>
            <input
              id="transfer-quantity"
              min="0.001"
              onChange={(event) => setQuantity(event.target.value)}
              step="0.001"
              type="number"
              value={quantity}
            />
          </label>
          <button
            className="scanner-confirm-button"
            disabled={!canConfirm || transfer.isPending || !quantity}
            onClick={confirmTransfer}
            type="button"
          >
            {transfer.isPending ? "Moving stock..." : "Confirm transfer"}
          </button>
        </article>
      </section>
    </>
  );
}
