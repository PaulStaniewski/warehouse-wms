import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft, Camera } from "lucide-react";
import { Link } from "react-router-dom";

import {
  useScannerCartItems,
  useScannerControlCart,
  useScannerControlFinish,
  useScannerControlTarget,
  useScannerPickingPrepare,
  useScannerPrintLabel,
} from "../api/queries";
import { storeScannerSession, useStoredScannerSession } from "../api/scannerSession";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import type { ScannerCartItem } from "../types/api";


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function toNumber(value: string | number) {
  return Number.parseFloat(String(value));
}

function formatQuantity(value: string | number) {
  const numberValue = toNumber(value);
  if (!Number.isFinite(numberValue)) {
    return String(value);
  }

  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(numberValue);
}

function itemRemaining(item: ScannerCartItem) {
  return toNumber(item.remaining_quantity);
}

export function ScannerControlPage() {
  const queryClient = useQueryClient();
  const activeSession = useStoredScannerSession();
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [cartInput, setCartInput] = useState("");
  const [controlCartCode, setControlCartCode] = useState(activeSession?.cart_code ?? "");
  const [productInput, setProductInput] = useState("");
  const [productCode, setProductCode] = useState("");
  const [printerCode, setPrinterCode] = useState("ZEBRA-01");
  const [quantity, setQuantity] = useState("1");
  const [cameraMode, setCameraMode] = useState<"cart" | "product" | null>(null);
  const cartInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);
  const controlCart = useScannerControlCart(controlCartCode);
  const controlSession = controlCart.data?.session ?? activeSession;
  const fallbackCartItems = useScannerCartItems(controlCart.data ? undefined : activeSession?.id);
  const items = controlCart.data?.items ?? fallbackCartItems.data?.items ?? [];
  const target = useScannerControlTarget(controlSession?.id, productCode);
  const printLabel = useScannerPrintLabel();
  const prepare = useScannerPickingPrepare();
  const finish = useScannerControlFinish();
  const selectedTarget = target.data?.candidates[0];
  const cartLoaded = Boolean(controlSession && controlCartCode);
  const totalPicked = items.reduce((sum, item) => sum + toNumber(item.quantity_picked), 0);
  const totalPrepared = items.reduce((sum, item) => sum + toNumber(item.quantity_prepared), 0);
  const totalRemaining = items.reduce((sum, item) => sum + itemRemaining(item), 0);
  const isControlComplete = items.length > 0 && totalRemaining === 0;
  const labelReady = Boolean(selectedTarget?.customer_label_ready);
  const selectedRemaining = selectedTarget ? itemRemaining(selectedTarget) : 0;
  const quantityNumber = Number.parseInt(quantity || "0", 10);
  const quantityIsValid = Number.isInteger(quantityNumber) && quantityNumber > 0 && quantityNumber <= selectedRemaining;

  useEffect(() => {
    if (controlCart.data?.session) {
      storeScannerSession(controlCart.data.session);
    }
  }, [controlCart.data?.session]);

  const refreshCart = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart", controlCartCode] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", controlSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-target", controlSession?.id, productCode] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-proformas"] }),
      queryClient.invalidateQueries({ queryKey: ["shipments"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }, [controlCartCode, controlSession?.id, productCode, queryClient]);

  const submitCart = useCallback((code: string) => {
    const trimmedCode = code.trim();
    if (!trimmedCode) {
      return;
    }
    setMessage(null);
    setControlCartCode(trimmedCode);
    setCartInput("");
    setProductInput("");
    setProductCode("");
  }, []);

  const submitProduct = useCallback((code: string) => {
    const trimmedCode = code.trim();
    if (!trimmedCode) {
      return;
    }
    setMessage(null);
    setProductCode(trimmedCode);
    setProductInput(trimmedCode);
  }, []);

  function handleCartSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitCart(cartInput);
  }

  function handleProductSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitProduct(productInput);
  }

  const handleCameraDetected = useCallback(async (code: string) => {
    const mode = cameraMode;
    setCameraMode(null);
    if (mode === "cart") {
      submitCart(code);
    }
    if (mode === "product") {
      submitProduct(code);
    }
  }, [cameraMode, submitCart, submitProduct]);

  function handleCameraClose() {
    const mode = cameraMode;
    setCameraMode(null);
    window.setTimeout(() => {
      if (mode === "cart") {
        cartInputRef.current?.focus();
      }
      if (mode === "product") {
        productInputRef.current?.focus();
      }
    }, 0);
  }

  function handleChangeCart() {
    setControlCartCode("");
    setCartInput("");
    setProductInput("");
    setProductCode("");
    setMessage(null);
    window.setTimeout(() => cartInputRef.current?.focus(), 0);
  }

  async function handlePrintLabel() {
    if (!controlSession || !selectedTarget) {
      return;
    }

    try {
      const result = await printLabel.mutateAsync({
        orderReference: selectedTarget.order_reference,
        printerCode,
        sessionId: controlSession.id,
      });
      setMessage({ type: "success", text: `${result.message} ${result.label.scan_code}` });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not print the label.") });
    }
  }

  async function handlePrepare() {
    if (!controlSession || !selectedTarget || !quantityIsValid) {
      return;
    }

    try {
      const result = await prepare.mutateAsync({
        code: selectedTarget.order_reference,
        productCode,
        quantity,
        routeRunId: selectedTarget.route_run,
        sessionId: controlSession.id,
      });
      setMessage({ type: "success", text: result.message });
      setQuantity("1");
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not prepare the item.") });
    }
  }

  async function handleFinish() {
    if (!controlSession || !isControlComplete) {
      return;
    }

    try {
      const result = await finish.mutateAsync({ sessionId: controlSession.id });
      storeScannerSession(null);
      setControlCartCode("");
      setProductCode("");
      setProductInput("");
      setMessage({ type: "success", text: result.message || "Control finished." });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not finish control.") });
    }
  }

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
        <Link to="/scanner/picking">Picking</Link>
      </div>

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <CameraBarcodeScanner
        isOpen={cameraMode !== null}
        onClose={handleCameraClose}
        onDetected={handleCameraDetected}
      />

      {!cartLoaded ? (
        <form className="scanner-workflow-panel scanner-workflow-panel--prepare" onSubmit={handleCartSubmit}>
          <header>
            <span>1</span>
            <h2>Control</h2>
          </header>
          <label htmlFor="control-cart-code">
            <span>Scan cart</span>
            <input
              autoComplete="off"
              autoFocus
              id="control-cart-code"
              onChange={(event) => setCartInput(event.target.value)}
              placeholder="WOZEK-01"
              ref={cartInputRef}
              value={cartInput}
            />
          </label>
          <button disabled={!cartInput.trim()} type="submit">
            Show cart
          </button>
          <button className="scanner-camera-button" onClick={() => setCameraMode("cart")} type="button">
            <Camera size={19} />
            Scan with camera
          </button>
        </form>
      ) : (
        <>
          {controlCart.isError && (
            <div className="scanner-message scanner-message--error">
              {getErrorMessage(controlCart.error, "This cart has no active work for control.")}
            </div>
          )}

          <section className="scanner-control-header">
            <div>
              <h1>{controlSession?.cart_code ?? controlCartCode}</h1>
              <p>
                {items.length} lines · {formatQuantity(totalPicked)} picked · {formatQuantity(totalPrepared)} prepared ·{" "}
                {formatQuantity(totalRemaining)} remaining
              </p>
            </div>
            <button type="button" onClick={handleChangeCart}>
              Change cart
            </button>
          </section>

          <form className="scanner-workflow-panel scanner-workflow-panel--prepare scanner-workflow-panel--compact" onSubmit={handleProductSubmit}>
            <header>
              <span>2</span>
              <h2>Scan product</h2>
            </header>
            <label htmlFor="control-product-code">
              <span>Product</span>
              <input
                autoComplete="off"
                autoFocus
                disabled={!controlSession}
                id="control-product-code"
                onChange={(event) => setProductInput(event.target.value)}
                placeholder="SKU, barcode, or index"
                ref={productInputRef}
                value={productInput}
              />
            </label>
            <button disabled={!controlSession || !productInput.trim()} type="submit">
              Show target
            </button>
            <button className="scanner-camera-button" disabled={!controlSession} onClick={() => setCameraMode("product")} type="button">
              <Camera size={19} />
              Scan with camera
            </button>
          </form>

          {target.isError && (
            <div className="scanner-message scanner-message--error">
              {getErrorMessage(target.error, "Product is not available on this cart.")}
            </div>
          )}

          {selectedTarget && (
            <section className="scanner-active-control-item">
              <header>
                <div>
                  <span>{selectedTarget.product_sku}</span>
                  <h2>{selectedTarget.product_name}</h2>
                </div>
                {labelReady && (
                  <strong>
                    Customer label ready
                    {selectedTarget.customer_label_scan_code ? ` ${selectedTarget.customer_label_scan_code}` : ""}
                  </strong>
                )}
              </header>
              <div className="scanner-target-block">
                <span>Target</span>
                <strong>{selectedTarget.customer_name || "-"}</strong>
                <small>{selectedTarget.order_reference}</small>
              </div>
              <dl>
                <div>
                  <dt>Picked</dt>
                  <dd>{formatQuantity(selectedTarget.quantity_picked)}</dd>
                </div>
                <div>
                  <dt>Prepared</dt>
                  <dd>{formatQuantity(selectedTarget.quantity_prepared)}</dd>
                </div>
                <div>
                  <dt>Remaining</dt>
                  <dd>{formatQuantity(selectedTarget.remaining_quantity)}</dd>
                </div>
              </dl>
              <div className="scanner-control-actions">
                <label htmlFor="control-quantity">
                  <span>Quantity</span>
                  <input
                    id="control-quantity"
                    inputMode="numeric"
                    onChange={(event) => setQuantity(event.target.value.replace(/\D/g, ""))}
                    pattern="[0-9]*"
                    type="text"
                    value={quantity}
                  />
                </label>
                <label htmlFor="printer-code">
                  <span>Printer</span>
                  <input id="printer-code" onChange={(event) => setPrinterCode(event.target.value)} value={printerCode} />
                </label>
                {!labelReady && (
                  <button disabled={!printerCode.trim() || printLabel.isPending} onClick={handlePrintLabel} type="button">
                    Print label
                  </button>
                )}
                <button disabled={!labelReady || !quantityIsValid || prepare.isPending} onClick={handlePrepare} type="button">
                  Prepare item
                </button>
              </div>
              {!quantityIsValid && <small className="scanner-inline-hint">Quantity must be between 1 and remaining picked quantity.</small>}
            </section>
          )}

          <section className="scanner-compact-list">
            <h2>Cart contents</h2>
            {items.length === 0 ? (
              <div className="state-box">No picked items on this cart.</div>
            ) : (
              items.map((item) => {
                const completed = itemRemaining(item) <= 0;
                const selected = selectedTarget?.id === item.id;
                return (
                  <article
                    className={`scanner-compact-row ${completed ? "scanner-compact-row--completed" : ""} ${
                      selected ? "scanner-compact-row--selected" : ""
                    }`}
                    key={item.id}
                  >
                    <div>
                      <strong>{item.product_sku}</strong>
                      <span>{item.product_name}</span>
                      <small>{item.order_reference} · {item.customer_name || "-"}</small>
                    </div>
                    <div>
                      <strong>{formatQuantity(item.quantity_picked)} / {formatQuantity(item.quantity_prepared)}</strong>
                      <small>{completed ? "Completed" : `Remaining ${formatQuantity(item.remaining_quantity)}`}</small>
                    </div>
                  </article>
                );
              })
            )}
          </section>

          <button
            className="scanner-confirm-button scanner-finish-button"
            disabled={!controlSession || !isControlComplete || finish.isPending}
            onClick={handleFinish}
            type="button"
          >
            Finish control
          </button>
          {!isControlComplete && <p className="scanner-inline-hint">Prepare all cart items before finishing control.</p>}
        </>
      )}
    </>
  );
}
