import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";

import {
  useScannerLocationContents,
  useScannerProductLookup,
  useScannerQuickTransfer,
} from "../api/queries";
import { ScannerScanInput, ScannerStatusMessage, ScannerStepIndicator } from "../components/scanner/ScannerUi";


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
  const activeStep = !sourceLookup.data ? 1 : !productInSource ? 2 : !targetLookup.data ? 3 : 4;

  function submitSource(value: string) {
    setMessage(null);
    setSourceCode(value);
    setProductCode("");
    setProductInput("");
    setTargetCode("");
    setTargetInput("");
  }

  function submitProduct(value: string) {
    setMessage(null);
    setProductCode(value);
    setTargetCode("");
    setTargetInput("");
  }

  function submitTarget(value: string) {
    setMessage(null);
    setTargetCode(value);
  }

  function resetTransfer() {
    setSourceInput("");
    setSourceCode("");
    setProductInput("");
    setProductCode("");
    setTargetInput("");
    setTargetCode("");
    setQuantity("1");
    setMessage(null);
  }

  async function confirmTransfer() {
    if (transfer.isPending) {
      return;
    }
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

      {message && <ScannerStatusMessage type={message.type}>{message.text}</ScannerStatusMessage>}

      <section className="scanner-tool-panel">
        <div>
          <p>Quick transfer</p>
          <h1>Move stock</h1>
        </div>
        <ScannerStepIndicator
          steps={[
            { label: "Source", isActive: activeStep === 1, isComplete: Boolean(sourceLookup.data) },
            { label: "Product", isActive: activeStep === 2, isComplete: Boolean(productInSource) },
            { label: "Target", isActive: activeStep === 3, isComplete: Boolean(targetLookup.data) },
            { label: "Confirm", isActive: activeStep === 4, isComplete: message?.type === "success" },
          ]}
        />
      </section>

      <section className="scanner-step-list">
        <article className="scanner-step-card">
          <header>
            <span>1</span>
            <h2>Scan source location</h2>
            {sourceLookup.data && <CheckCircle2 size={24} />}
          </header>
          <ScannerScanInput
            autoFocus={activeStep === 1}
            id="source-location"
            isPending={sourceLookup.isFetching}
            label="Source location"
            onChange={setSourceInput}
            onSubmit={submitSource}
            placeholder="Example A-01-01"
            value={sourceInput}
          />
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
          <ScannerScanInput
            autoFocus={activeStep === 2}
            disabled={!sourceLookup.data}
            id="transfer-product"
            isPending={productLookup.isFetching}
            label="Product SKU or barcode"
            onChange={setProductInput}
            onSubmit={submitProduct}
            placeholder="Example FILTR-001"
            value={productInput}
          />
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
          <ScannerScanInput
            autoFocus={activeStep === 3}
            disabled={!productInSource}
            id="target-location"
            isPending={targetLookup.isFetching}
            label="Target location"
            onChange={setTargetInput}
            onSubmit={submitTarget}
            placeholder="Example A-02-01"
            value={targetInput}
          />
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
          {message?.type === "success" && (
            <button className="scanner-secondary-button" onClick={resetTransfer} type="button">
              New transfer
            </button>
          )}
        </article>
      </section>
    </>
  );
}
