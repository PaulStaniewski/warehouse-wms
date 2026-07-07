import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { ArrowLeft, Camera, CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import {
  useScannerReceivingComplete,
  useScannerReceivingCurrent,
  useScannerReceivingPutAway,
  useScannerReceivingScanProduct,
  useScannerReceivingStart,
} from "../api/queries";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import type { ScannerReceivingSession, TransferPalletManifestItem } from "../types/api";

const RECEIVING_SESSION_KEY = "warehouse-wms-receiving-session-id";

type CameraMode = "pallet" | "product" | "location" | null;

function getStoredReceivingSessionId() {
  const rawValue = window.localStorage.getItem(RECEIVING_SESSION_KEY);
  const parsed = rawValue ? Number(rawValue) : null;
  return parsed && Number.isFinite(parsed) ? parsed : null;
}

function storeReceivingSessionId(value: number | null) {
  if (value) {
    window.localStorage.setItem(RECEIVING_SESSION_KEY, String(value));
    return;
  }
  window.localStorage.removeItem(RECEIVING_SESSION_KEY);
}

function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : fallback;
}

function isStaleSessionError(error: unknown) {
  return axios.isAxiosError(error) && [400, 404].includes(error.response?.status ?? 0);
}

function formatQuantity(value: number) {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(value);
}

function manifestRowClassName(item: TransferPalletManifestItem) {
  if (item.remaining_quantity === 0) {
    return "scanner-compact-row scanner-compact-row--done";
  }
  if (item.received_quantity > 0) {
    return "scanner-compact-row scanner-compact-row--active";
  }
  return "scanner-compact-row";
}

function PalletSummary({ session }: { session: ScannerReceivingSession }) {
  const remaining = session.summary.remaining_quantity;

  return (
    <section className="scanner-cart-panel scanner-cart-panel--compact">
      <div>
        <span>Pallet</span>
        <strong>{session.pallet.scan_code}</strong>
        <small>
          {session.pallet.source_branch_code} to {session.pallet.destination_branch_code}
        </small>
      </div>
      <div>
        <span>Received</span>
        <strong>
          {formatQuantity(session.summary.received_quantity)} / {formatQuantity(session.summary.expected_quantity)}
        </strong>
        <small>{remaining === 0 ? "Ready to complete" : `${formatQuantity(remaining)} pcs left`}</small>
      </div>
    </section>
  );
}

export function ScannerReceivingPage() {
  const queryClient = useQueryClient();
  const [receivingSessionId, setReceivingSessionId] = useState<number | null>(() => getStoredReceivingSessionId());
  const [palletCode, setPalletCode] = useState("");
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [productCode, setProductCode] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [locationCode, setLocationCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [cameraMode, setCameraMode] = useState<CameraMode>(null);
  const palletInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);
  const locationInputRef = useRef<HTMLInputElement | null>(null);

  const currentSession = useScannerReceivingCurrent(receivingSessionId);
  const startReceiving = useScannerReceivingStart();
  const scanProduct = useScannerReceivingScanProduct();
  const putAway = useScannerReceivingPutAway();
  const completeReceiving = useScannerReceivingComplete();
  const session = currentSession.data?.receiving_session;
  const isRestoringSession = Boolean(receivingSessionId && currentSession.isLoading);

  useEffect(() => {
    storeReceivingSessionId(receivingSessionId);
  }, [receivingSessionId]);

  useEffect(() => {
    if (receivingSessionId && currentSession.isError && isStaleSessionError(currentSession.error)) {
      setReceivingSessionId(null);
      setPalletCode("");
      setProductCode("");
      setLocationCode("");
      setMessage("Previous receiving session is no longer active.");
      setErrorMessage(null);
    }
  }, [currentSession.error, currentSession.isError, receivingSessionId]);

  useEffect(() => {
    if (!receivingSessionId) {
      window.setTimeout(() => palletInputRef.current?.focus(), 0);
      return;
    }
    if (session?.state === "waiting_for_product") {
      window.setTimeout(() => productInputRef.current?.focus(), 0);
    }
    if (session?.state === "waiting_for_location") {
      window.setTimeout(() => locationInputRef.current?.focus(), 0);
    }
  }, [receivingSessionId, session?.state]);

  const refetchCurrent = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["scanner-receiving-current"] });
  }, [queryClient]);

  function applySessionData(response: { receiving_session: ScannerReceivingSession }) {
    queryClient.setQueryData(["scanner-receiving-current", response.receiving_session.id], response);
  }

  async function handleStart(event?: FormEvent<HTMLFormElement>, scannedCode?: string) {
    event?.preventDefault();
    const code = (scannedCode ?? palletCode).trim();
    if (!code) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await startReceiving.mutateAsync({ palletCode: code, workerCode: workerCode.trim() || "DEMO" });
      setReceivingSessionId(response.receiving_session.id);
      setPalletCode(response.receiving_session.pallet.scan_code);
      applySessionData(response);
      setMessage(response.message || "Pallet receiving started.");
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not start pallet receiving."));
    }
  }

  async function handleScanProduct(event?: FormEvent<HTMLFormElement>, scannedCode?: string) {
    event?.preventDefault();
    if (!session) {
      return;
    }
    const code = (scannedCode ?? productCode).trim();
    if (!code) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await scanProduct.mutateAsync({
        productCode: code,
        quantity,
        receivingSessionId: session.id,
      });
      setProductCode("");
      setQuantity("1");
      applySessionData(response);
      setMessage(response.message || "Product confirmed.");
      await refetchCurrent();
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not confirm product."));
    }
  }

  async function handlePutAway(event?: FormEvent<HTMLFormElement>, scannedCode?: string) {
    event?.preventDefault();
    if (!session) {
      return;
    }
    const code = (scannedCode ?? locationCode).trim();
    if (!code) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await putAway.mutateAsync({ locationCode: code, receivingSessionId: session.id });
      setLocationCode("");
      applySessionData(response);
      setMessage(response.message || "Product put away.");
      await refetchCurrent();
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not put away product."));
    }
  }

  async function handleComplete() {
    if (!session) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await completeReceiving.mutateAsync({ receivingSessionId: session.id });
      setMessage(response.message || "Pallet receiving completed.");
      setReceivingSessionId(null);
      setPalletCode("");
      await refetchCurrent();
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not complete pallet receiving."));
    }
  }

  function handleResetSession() {
    setReceivingSessionId(null);
    setPalletCode("");
    setProductCode("");
    setLocationCode("");
    setMessage(null);
    setErrorMessage(null);
  }

  const handleCameraDetected = useCallback(
    async (code: string) => {
      const mode = cameraMode;
      setCameraMode(null);
      if (mode === "pallet") {
        await handleStart(undefined, code);
      }
      if (mode === "product") {
        await handleScanProduct(undefined, code);
      }
      if (mode === "location") {
        await handlePutAway(undefined, code);
      }
    },
    [cameraMode, palletCode, productCode, quantity, session, workerCode],
  );

  const isBusy =
    startReceiving.isPending || scanProduct.isPending || putAway.isPending || completeReceiving.isPending || currentSession.isFetching;

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
      </div>

      <CameraBarcodeScanner isOpen={cameraMode !== null} onClose={() => setCameraMode(null)} onDetected={handleCameraDetected} />

      <section className="scanner-home-header scanner-home-header--compact">
        <p>Inter-branch transfer</p>
        <h1>Pallet receiving</h1>
      </section>

      {message && <div className="scanner-message scanner-message--success">{message}</div>}
      {errorMessage && <div className="scanner-message scanner-message--error">{errorMessage}</div>}
      {currentSession.isError && !isStaleSessionError(currentSession.error) && (
        <div className="scanner-message scanner-message--error">
          {getErrorMessage(currentSession.error, "Could not restore receiving session.")}
        </div>
      )}

      {isRestoringSession && (
        <section className="scanner-workflow-panel">
          <header>
            <span>...</span>
            <h2>Restoring pallet receiving</h2>
          </header>
          <p className="scanner-inline-hint">Loading active pallet state from the backend.</p>
        </section>
      )}

      {!session && !isRestoringSession && (
        <form className="scanner-workflow-panel" onSubmit={handleStart}>
          <header>
            <span>1</span>
            <h2>Scan pallet</h2>
          </header>
          <label htmlFor="worker-code">
            <span>Worker code</span>
            <input
              autoComplete="off"
              id="worker-code"
              onChange={(event) => setWorkerCode(event.target.value)}
              value={workerCode}
            />
          </label>
          <label htmlFor="pallet-code">
            <span>Pallet barcode</span>
            <input
              autoComplete="off"
              autoFocus
              id="pallet-code"
              onChange={(event) => setPalletCode(event.target.value)}
              placeholder="PAL-GDA-GDY-001"
              ref={palletInputRef}
              value={palletCode}
            />
          </label>
          <button disabled={!palletCode.trim() || startReceiving.isPending} type="submit">
            {startReceiving.isPending ? "Starting..." : "Start receiving"}
          </button>
          <button className="scanner-camera-button" onClick={() => setCameraMode("pallet")} type="button">
            <Camera size={19} />
            Scan with camera
          </button>
        </form>
      )}

      {session && (
        <>
          <PalletSummary session={session} />

          {session.state === "waiting_for_product" && (
            <form className="scanner-workflow-panel" onSubmit={handleScanProduct}>
              <header>
                <span>2</span>
                <h2>Scan product</h2>
              </header>
              <label htmlFor="product-code">
                <span>Product barcode or SKU</span>
                <input
                  autoComplete="off"
                  id="product-code"
                  onChange={(event) => setProductCode(event.target.value)}
                  placeholder="FILTR-001 or barcode"
                  ref={productInputRef}
                  value={productCode}
                />
              </label>
              <label htmlFor="receive-quantity">
                <span>Quantity</span>
                <input
                  id="receive-quantity"
                  inputMode="numeric"
                  min="1"
                  onChange={(event) => setQuantity(event.target.value)}
                  type="number"
                  value={quantity}
                />
              </label>
              <button disabled={!productCode.trim() || isBusy} type="submit">
                Confirm product
              </button>
              <button className="scanner-camera-button" onClick={() => setCameraMode("product")} type="button">
                <Camera size={19} />
                Scan with camera
              </button>
            </form>
          )}

          {session.state === "waiting_for_location" && session.pending && (
            <form className="scanner-workflow-panel" onSubmit={handlePutAway}>
              <header>
                <span>3</span>
                <h2>Scan destination</h2>
              </header>
              <section className="scanner-confirmed-product">
                <CheckCircle2 size={24} />
                <div>
                  <strong>{session.pending.product_sku}</strong>
                  <span>
                    {session.pending.product_name} - Quantity {formatQuantity(session.pending.quantity)}
                  </span>
                </div>
              </section>
              <label htmlFor="destination-location">
                <span>Destination location</span>
                <input
                  autoComplete="off"
                  id="destination-location"
                  onChange={(event) => setLocationCode(event.target.value)}
                  placeholder="A-01-01"
                  ref={locationInputRef}
                  value={locationCode}
                />
              </label>
              <button disabled={!locationCode.trim() || isBusy} type="submit">
                Put away
              </button>
              <button className="scanner-camera-button" onClick={() => setCameraMode("location")} type="button">
                <Camera size={19} />
                Scan with camera
              </button>
            </form>
          )}

          <section className="scanner-compact-list">
            <header className="scanner-list-heading">
              <h2>Manifest</h2>
              <span>{session.pallet.transfer_reference}</span>
            </header>
            {session.manifest.map((item) => (
              <article className={manifestRowClassName(item)} key={item.id}>
                <div>
                  <strong>{item.product_sku}</strong>
                  <span>{item.product_name}</span>
                  <small>
                    Received {formatQuantity(item.received_quantity)} / {formatQuantity(item.expected_quantity)} pcs
                  </small>
                </div>
                <div>
                  <strong>{formatQuantity(item.remaining_quantity)}</strong>
                  <small>left</small>
                </div>
              </article>
            ))}
          </section>

          <button
            className="scanner-confirm-button"
            disabled={session.summary.remaining_quantity > 0 || completeReceiving.isPending}
            onClick={handleComplete}
            type="button"
          >
            {completeReceiving.isPending ? "Completing..." : "Complete pallet"}
          </button>
          <button className="scanner-secondary-button" onClick={handleResetSession} type="button">
            Change pallet
          </button>
        </>
      )}
    </>
  );
}
