import { type FormEvent, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import {
  useScannerCartItems,
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
  const [productInput, setProductInput] = useState("");
  const [productCode, setProductCode] = useState("");
  const [printerCode, setPrinterCode] = useState("ZEBRA-01");
  const [quantity, setQuantity] = useState("1");
  const cartItems = useScannerCartItems(activeSession?.id);
  const target = useScannerControlTarget(activeSession?.id, productCode);
  const printLabel = useScannerPrintLabel();
  const prepare = useScannerPickingPrepare();
  const finish = useScannerControlFinish();
  const selectedTarget = target.data?.candidates[0];

  async function refreshCart() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", activeSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-target", activeSession?.id, productCode] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }

  function handleProductSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setProductCode(productInput.trim());
  }

  async function handlePrintLabel() {
    if (!activeSession || !selectedTarget) {
      return;
    }

    try {
      const result = await printLabel.mutateAsync({
        orderReference: selectedTarget.order_reference,
        printerCode,
        sessionId: activeSession.id,
      });
      setMessage({ type: "success", text: result.message });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Nie można wydrukować etykiety.") });
    }
  }

  async function handlePrepare() {
    if (!activeSession || !selectedTarget) {
      return;
    }

    try {
      const result = await prepare.mutateAsync({
        code: selectedTarget.order_reference,
        productCode,
        quantity,
        routeRunId: selectedTarget.route_run,
        sessionId: activeSession.id,
      });
      setMessage({ type: "success", text: result.message });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Nie można zatwierdzić kontroli.") });
    }
  }

  async function handleFinish() {
    if (!activeSession) {
      return;
    }

    try {
      const result = await finish.mutateAsync({ sessionId: activeSession.id });
      storeScannerSession(null);
      setMessage({ type: "success", text: result.message || "Kontrola zakończona." });
      await refreshCart();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Nie można zakończyć kontroli.") });
    }
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
        <Link to="/scanner/picking">Pobranie</Link>
      </div>

      {!activeSession && (
        <div className="scanner-message scanner-message--error">
          Brak aktywnego wózka. <Link to="/scanner">Zeskanuj wózek</Link> przed kontrolą.
        </div>
      )}

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <section className="scanner-header-panel">
        <div>
          <p>Kontrola</p>
          <h1>
            Wózek <span>{activeSession?.cart_code ?? "brak"}</span>
          </h1>
        </div>
        <dl>
          <div>
            <dt>Items</dt>
            <dd>{cartItems.data?.items.length ?? 0}</dd>
          </div>
          <div>
            <dt>Worker</dt>
            <dd>{activeSession?.worker_code || "-"}</dd>
          </div>
        </dl>
      </section>

      <form className="scanner-workflow-panel scanner-workflow-panel--prepare" onSubmit={handleProductSubmit}>
        <header>
          <span>B</span>
          <h2>Kontrola</h2>
        </header>
        <label htmlFor="control-product-code">
          <span>Zeskanuj produkt</span>
          <input
            autoComplete="off"
            autoFocus={Boolean(activeSession)}
            disabled={!activeSession}
            id="control-product-code"
            onChange={(event) => setProductInput(event.target.value)}
            placeholder="SKU lub kod kreskowy"
            value={productInput}
          />
        </label>
        <button disabled={!activeSession || !productInput.trim()} type="submit">
          Pokaż cel
        </button>
      </form>

      {target.isError && <div className="scanner-message scanner-message--error">{getErrorMessage(target.error, "Brak produktu na wózku.")}</div>}

      {selectedTarget && (
        <section className="scanner-result-card">
          <div>
            <span>Produkt</span>
            <strong>{selectedTarget.product_sku}</strong>
          </div>
          <div>
            <span>Zamówienie</span>
            <strong>{selectedTarget.order_reference}</strong>
          </div>
          <div>
            <span>Klient</span>
            <strong>{selectedTarget.customer_name || "-"}</strong>
          </div>
          <div>
            <span>Pozostało</span>
            <strong>{selectedTarget.remaining_quantity}</strong>
          </div>
        </section>
      )}

      <section className="scanner-control-actions">
        <label htmlFor="printer-code">
          <span>Zeskanuj Zebrę / drukarkę</span>
          <input
            disabled={!selectedTarget}
            id="printer-code"
            onChange={(event) => setPrinterCode(event.target.value)}
            value={printerCode}
          />
        </label>
        <label htmlFor="control-quantity">
          <span>Ilość</span>
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
          Drukuj etykietę
        </button>
        <button disabled={!selectedTarget || !quantity || prepare.isPending} onClick={handlePrepare} type="button">
          Zatwierdź kontrolę
        </button>
      </section>

      <section className="scanner-list">
        {(cartItems.data?.items ?? []).length === 0 ? (
          <div className="state-box">Brak pobranych pozycji na aktywnym wózku.</div>
        ) : (
          cartItems.data?.items.map((item) => (
            <article className="scanner-list-row" key={item.id}>
              <div>
                <span>{item.product_sku}</span>
                <strong>{item.product_name}</strong>
                <small>{item.order_reference} / {item.customer_name || "-"}</small>
              </div>
              <div>
                <span>Pobrane / przygotowane</span>
                <strong>{item.quantity_picked} / {item.quantity_prepared}</strong>
                <small>Pozostało {item.remaining_quantity}</small>
              </div>
            </article>
          ))
        )}
      </section>

      <button className="scanner-confirm-button scanner-finish-button" disabled={!activeSession || finish.isPending} onClick={handleFinish} type="button">
        Zakończ kontrolę
      </button>
    </>
  );
}
