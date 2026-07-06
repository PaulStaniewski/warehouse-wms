import { type FormEvent, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
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


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
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
  const controlCart = useScannerControlCart(controlCartCode);
  const controlSession = controlCart.data?.session ?? activeSession;
  const fallbackCartItems = useScannerCartItems(controlCart.data ? undefined : activeSession?.id);
  const items = controlCart.data?.items ?? fallbackCartItems.data?.items ?? [];
  const target = useScannerControlTarget(controlSession?.id, productCode);
  const printLabel = useScannerPrintLabel();
  const prepare = useScannerPickingPrepare();
  const finish = useScannerControlFinish();
  const selectedTarget = target.data?.candidates[0];

  useEffect(() => {
    if (controlCart.data?.session) {
      storeScannerSession(controlCart.data.session);
    }
  }, [controlCart.data?.session]);

  async function refreshCart() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart", controlCartCode] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", controlSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-target", controlSession?.id, productCode] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }

  function handleCartSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setControlCartCode(cartInput.trim());
  }

  function handleProductSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setProductCode(productInput.trim());
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
      setMessage({ type: "success", text: result.message });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not print the label.") });
    }
  }

  async function handlePrepare() {
    if (!controlSession || !selectedTarget) {
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
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not prepare the item.") });
    }
  }

  async function handleFinish() {
    if (!controlSession) {
      return;
    }

    try {
      const result = await finish.mutateAsync({ sessionId: controlSession.id });
      storeScannerSession(null);
      setMessage({ type: "success", text: result.message || "Control finished." });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not finish control.") });
    }
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
        <Link to="/scanner/picking">Picking</Link>
      </div>

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <form className="scanner-workflow-panel scanner-workflow-panel--prepare" onSubmit={handleCartSubmit}>
        <header>
          <span>1</span>
          <h2>Control</h2>
        </header>
        <label htmlFor="control-cart-code">
          <span>Scan cart</span>
          <input
            autoComplete="off"
            autoFocus={!controlSession}
            id="control-cart-code"
            onChange={(event) => setCartInput(event.target.value)}
            placeholder="WOZEK-01"
            value={cartInput}
          />
        </label>
        <button disabled={!cartInput.trim()} type="submit">
          Show cart
        </button>
      </form>

      {controlCart.isError && (
        <div className="scanner-message scanner-message--error">
          {getErrorMessage(controlCart.error, "This cart has no active work for control.")}
        </div>
      )}

      <section className="scanner-header-panel">
        <div>
          <p>Control</p>
          <h1>
            Cart <span>{controlSession?.cart_code ?? "none"}</span>
          </h1>
        </div>
        <dl>
          <div>
            <dt>Items</dt>
            <dd>{items.length}</dd>
          </div>
          <div>
            <dt>Worker</dt>
            <dd>{controlSession?.worker_code || "-"}</dd>
          </div>
        </dl>
      </section>

      <form className="scanner-workflow-panel scanner-workflow-panel--prepare" onSubmit={handleProductSubmit}>
        <header>
          <span>2</span>
          <h2>Produkt</h2>
        </header>
        <label htmlFor="control-product-code">
          <span>Scan product</span>
          <input
            autoComplete="off"
            autoFocus={Boolean(controlSession)}
            disabled={!controlSession}
            id="control-product-code"
            onChange={(event) => setProductInput(event.target.value)}
            placeholder="SKU or barcode"
            value={productInput}
          />
        </label>
        <button disabled={!controlSession || !productInput.trim()} type="submit">
          Show target
        </button>
      </form>

      {target.isError && (
        <div className="scanner-message scanner-message--error">
          {getErrorMessage(target.error, "Product is not available on this cart.")}
        </div>
      )}

      {selectedTarget && (
        <section className="scanner-result-card">
          <div>
            <span>Produkt</span>
            <strong>{selectedTarget.product_sku}</strong>
          </div>
          <div>
            <span>Order</span>
            <strong>{selectedTarget.order_reference}</strong>
          </div>
          <div>
            <span>Klient</span>
            <strong>{selectedTarget.customer_name || "-"}</strong>
          </div>
          <div>
            <span>Remaining</span>
            <strong>{selectedTarget.remaining_quantity}</strong>
          </div>
        </section>
      )}

      <section className="scanner-control-actions">
        <label htmlFor="printer-code">
          <span>Scan Zebra / printer</span>
          <input
            disabled={!selectedTarget}
            id="printer-code"
            onChange={(event) => setPrinterCode(event.target.value)}
            value={printerCode}
          />
        </label>
        <label htmlFor="control-quantity">
          <span>Quantity</span>
          <input
            disabled={!selectedTarget}
            id="control-quantity"
            min="0.001"
            onChange={(event) => setQuantity(event.target.value)}
            step="0.001"
            type="number"
            value={quantity}
          />
        </label>
        <button disabled={!selectedTarget || !printerCode.trim() || printLabel.isPending} onClick={handlePrintLabel} type="button">
          Print label
        </button>
        <button disabled={!selectedTarget || !quantity || prepare.isPending} onClick={handlePrepare} type="button">
          Prepare item
        </button>
      </section>

      <section className="scanner-list">
        {items.length === 0 ? (
          <div className="state-box">No picked items on this cart.</div>
        ) : (
          items.map((item) => (
            <article className="scanner-list-row" key={item.id}>
              <div>
                <span>{item.product_sku}</span>
                <strong>{item.product_name}</strong>
                <small>{item.order_reference} / {item.customer_name || "-"}</small>
              </div>
              <div>
                <span>Pobrane / przygotowane</span>
                <strong>{item.quantity_picked} / {item.quantity_prepared}</strong>
                <small>Remaining {item.remaining_quantity}</small>
              </div>
            </article>
          ))
        )}
      </section>

      <button className="scanner-confirm-button scanner-finish-button" disabled={!controlSession || finish.isPending} onClick={handleFinish} type="button">
        Finish control
      </button>
    </>
  );
}
