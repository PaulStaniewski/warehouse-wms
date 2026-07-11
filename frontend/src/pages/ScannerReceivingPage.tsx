import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { ArrowLeft, Camera, CheckCircle2 } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import {
  useScannerReceivingClose,
  useScannerReceivingCurrent,
  useScannerReceivingPutAway,
  useScannerReceivingScanProduct,
  useScannerReceivingStart,
  useConfirmTransferDiscrepancyShortage,
  usePrintTransferDiscrepancyReport,
  useRecoverTransferDiscrepancyItem,
} from "../api/queries";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import type { ScannerReceivingSession, TransferPalletManifestItem } from "../types/api";

const RECEIVING_SESSION_KEY = "warehouse-wms-receiving-session-id";

type CameraMode =
  | "pallet"
  | "product"
  | "location"
  | "printer"
  | "recovery-product"
  | "recovery-location"
  | "shortage-product"
  | null;

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
        <small>{remaining === 0 ? "Ready to close" : `${formatQuantity(remaining)} pcs left`}</small>
      </div>
    </section>
  );
}

export function ScannerReceivingPage() {
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [receivingSessionId, setReceivingSessionId] = useState<number | null>(() => getStoredReceivingSessionId());
  const [palletCode, setPalletCode] = useState(() => searchParams.get("pallet") ?? "");
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [productCode, setProductCode] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [locationCode, setLocationCode] = useState("");
  const [printerCode, setPrinterCode] = useState("ZEBRA-01");
  const [recoveryProductCode, setRecoveryProductCode] = useState("");
  const [recoveryLocationCode, setRecoveryLocationCode] = useState("");
  const [recoveryQuantity, setRecoveryQuantity] = useState("1");
  const [shortageProductCode, setShortageProductCode] = useState("");
  const [shortageQuantity, setShortageQuantity] = useState("1");
  const [shortageReview, setShortageReview] = useState(false);
  const [shortageOperationId, setShortageOperationId] = useState<string | null>(null);
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
  const closeReceiving = useScannerReceivingClose();
  const printReport = usePrintTransferDiscrepancyReport();
  const recoverItem = useRecoverTransferDiscrepancyItem();
  const confirmShortage = useConfirmTransferDiscrepancyShortage();
  const session = currentSession.data?.receiving_session;
  const [closeResult, setCloseResult] = useState<{
    result?: "exact" | "discrepancy";
    session: ScannerReceivingSession;
    message?: string;
  } | null>(null);
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

  async function handleClosePallet() {
    if (!session) {
      return;
    }
    if (session.state === "waiting_for_location") {
      setErrorMessage("Finish or cancel the pending put-away before closing the pallet.");
      return;
    }
    if (session.summary.remaining_quantity > 0) {
      const confirmed = window.confirm(
        `This pallet is incomplete.\n\nExpected: ${formatQuantity(session.summary.expected_quantity)}\nReceived: ${formatQuantity(session.summary.received_quantity)}\nMissing: ${formatQuantity(session.summary.remaining_quantity)}\n\nClosing the pallet will create a discrepancy case.`,
      );
      if (!confirmed) {
        return;
      }
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await closeReceiving.mutateAsync({ receivingSessionId: session.id });
      setCloseResult({ result: response.result, session: response.receiving_session, message: response.message });
      setMessage(response.message || "Pallet closed.");
      setReceivingSessionId(null);
      setPalletCode("");
      await refetchCurrent();
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not close pallet."));
    }
  }

  async function handlePrintReport(event?: FormEvent<HTMLFormElement>, scannedCode?: string) {
    event?.preventDefault();
    const discrepancy = closeResult?.session.discrepancy;
    const code = (scannedCode ?? printerCode).trim();
    if (!discrepancy || !code) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await printReport.mutateAsync({
        discrepancyId: discrepancy.id,
        printerCode: code,
        workerCode,
      });
      setPrinterCode(code);
      setMessage(
        response.first_print
          ? "Report printed. Shortage posted to UNCONFIRMED."
          : "Report reprinted. UNCONFIRMED was not posted again.",
      );
      setCloseResult({
        result: "discrepancy",
        session: {
          ...closeResult.session,
          discrepancy: {
            ...discrepancy,
            status: response.discrepancy.status,
            report_printed_at: response.discrepancy.report_printed_at,
            report_print_count: response.discrepancy.report_print_count,
            last_report_printer_code: response.discrepancy.last_report_printer_code,
            shortage_posted_at: response.discrepancy.shortage_posted_at,
          },
        },
        message: response.message,
      });
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not print discrepancy report."));
    }
  }

  async function handleRecoverItem(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const discrepancy = closeResult?.session.discrepancy;
    if (!discrepancy) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await recoverItem.mutateAsync({
        clientOperationId: crypto.randomUUID(),
        destinationLocationCode: recoveryLocationCode.trim(),
        discrepancyId: discrepancy.id,
        productCode: recoveryProductCode.trim(),
        quantity: recoveryQuantity,
        workerCode,
      });
      setMessage(
        Number(response.recovery.total_remaining_quantity) === 0
          ? "Item recovered. All missing quantity has been recovered."
          : `Item recovered. Remaining discrepancy quantity ${response.recovery.total_remaining_quantity}.`,
      );
      setRecoveryProductCode("");
      setRecoveryLocationCode("");
      setRecoveryQuantity("1");
      setCloseResult({
        ...closeResult,
        session: {
          ...closeResult.session,
          discrepancy: discrepancy
            ? {
                ...discrepancy,
                status: response.recovery.status,
              }
            : null,
        },
      });
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not record recovered item."));
    }
  }

  function handleReviewShortage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!shortageProductCode.trim() || !shortageQuantity.trim()) {
      return;
    }
    setShortageOperationId((current) => current ?? crypto.randomUUID());
    setShortageReview(true);
    setMessage(null);
    setErrorMessage(null);
  }

  async function handleConfirmShortage() {
    const discrepancy = closeResult?.session.discrepancy;
    if (!discrepancy || !shortageOperationId) {
      return;
    }
    setMessage(null);
    setErrorMessage(null);
    try {
      const response = await confirmShortage.mutateAsync({
        clientOperationId: shortageOperationId,
        discrepancyId: discrepancy.id,
        productCode: shortageProductCode.trim(),
        quantity: shortageQuantity,
        workerCode,
      });
      const isFinal = Number(response.confirmation.total_remaining_quantity) === 0;
      setMessage(
        isFinal
          ? `Shortage confirmed. Status ${response.confirmation.status}.`
          : `Shortage recorded. Remaining investigation quantity ${response.confirmation.total_remaining_quantity}.`,
      );
      setShortageProductCode("");
      setShortageQuantity("1");
      setShortageReview(false);
      setShortageOperationId(null);
      setCloseResult({
        ...closeResult,
        session: {
          ...closeResult.session,
          discrepancy: {
            ...discrepancy,
            status: response.confirmation.status,
            total_recovered_quantity: Number(response.confirmation.total_recovered_quantity),
            total_confirmed_shortage_quantity: Number(response.confirmation.total_confirmed_shortage_quantity),
            total_remaining_quantity: Number(response.confirmation.total_remaining_quantity),
          },
        },
      });
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "Could not confirm shortage."));
    }
  }

  function handleResetSession() {
    setReceivingSessionId(null);
    setPalletCode("");
    setProductCode("");
    setLocationCode("");
    setCloseResult(null);
    setShortageProductCode("");
    setShortageQuantity("1");
    setShortageReview(false);
    setShortageOperationId(null);
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
      if (mode === "printer") {
        await handlePrintReport(undefined, code);
      }
      if (mode === "recovery-product") {
        setRecoveryProductCode(code);
      }
      if (mode === "recovery-location") {
        setRecoveryLocationCode(code);
      }
      if (mode === "shortage-product") {
        setShortageProductCode(code);
      }
    },
    [cameraMode, closeResult, palletCode, printerCode, productCode, quantity, session, workerCode],
  );

  const isBusy =
    startReceiving.isPending ||
    scanProduct.isPending ||
    putAway.isPending ||
    closeReceiving.isPending ||
    printReport.isPending ||
    recoverItem.isPending ||
    confirmShortage.isPending ||
    currentSession.isFetching;

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

      {closeResult && (
        <section className="scanner-contents-header">
          <span>{closeResult.result === "discrepancy" ? "PALLET CLOSED WITH DISCREPANCY" : "PALLET RECEIVED"}</span>
          <h1>{closeResult.session.pallet.scan_code}</h1>
          <p>
            Expected {formatQuantity(closeResult.session.summary.expected_quantity)} - Received{" "}
            {formatQuantity(closeResult.session.summary.received_quantity)}
            {closeResult.session.summary.remaining_quantity > 0
              ? ` - Missing ${formatQuantity(closeResult.session.summary.remaining_quantity)}`
              : " - No discrepancies"}
          </p>
          {closeResult.session.discrepancy && <small>Discrepancy {closeResult.session.discrepancy.reference}</small>}
        </section>
      )}

      {closeResult?.session.discrepancy && (
        <section className="scanner-compact-list">
          {closeResult.session.discrepancy.items.map((item) => (
            <article className="scanner-compact-row scanner-compact-row--active" key={item.id}>
              <div>
                <strong>{item.product_sku}</strong>
                <span>{item.product_name}</span>
                <small>
                  Expected {formatQuantity(Number(item.expected_quantity))} - Received {formatQuantity(Number(item.received_quantity))}
                </small>
              </div>
              <div>
                <strong>{formatQuantity(Number(item.discrepancy_quantity))}</strong>
                <small>missing</small>
              </div>
            </article>
          ))}
        </section>
      )}

      {closeResult?.session.discrepancy && (
        <form className="scanner-workflow-panel" onSubmit={handlePrintReport}>
          <header>
            <span>4</span>
            <h2>
              {closeResult.session.discrepancy.report_printed_at
                ? "Report printed"
                : "Discrepancy report required"}
            </h2>
          </header>
          {closeResult.session.discrepancy.report_printed_at ? (
            <section className="scanner-confirmed-product">
              <CheckCircle2 size={24} />
              <div>
                <strong>{closeResult.session.discrepancy.reference}</strong>
                <span>
                  Printer {closeResult.session.discrepancy.last_report_printer_code || "-"} - Status{" "}
                  {closeResult.session.discrepancy.status}
                </span>
              </div>
            </section>
          ) : (
            <p className="scanner-inline-hint">Scan printer to print the report and post the shortage to UNCONFIRMED.</p>
          )}
          <label htmlFor="discrepancy-printer-code">
            <span>Printer code</span>
            <input
              autoComplete="off"
              id="discrepancy-printer-code"
              onChange={(event) => setPrinterCode(event.target.value)}
              value={printerCode}
            />
          </label>
          <button disabled={!printerCode.trim() || printReport.isPending} type="submit">
            {printReport.isPending
              ? "Printing..."
              : closeResult.session.discrepancy.report_printed_at
                ? "Reprint report"
                : "Print discrepancy report"}
          </button>
          <button className="scanner-camera-button" onClick={() => setCameraMode("printer")} type="button">
            <Camera size={19} />
            Scan with camera
          </button>
          <Link className="scanner-secondary-button" to={`/wms/discrepancies/${closeResult.session.discrepancy.id}`}>
            View discrepancy
          </Link>
        </form>
      )}

      {closeResult?.session.discrepancy?.report_printed_at &&
        closeResult.session.discrepancy.status === "investigating" && (
          <>
            <form className="scanner-workflow-panel" onSubmit={handleRecoverItem}>
              <header>
                <span>5</span>
                <h2>Continue investigation</h2>
              </header>
              <label htmlFor="recovery-product-code">
                <span>Scan found product</span>
                <input
                  autoComplete="off"
                  id="recovery-product-code"
                  onChange={(event) => setRecoveryProductCode(event.target.value)}
                  placeholder="FILTR-001"
                  value={recoveryProductCode}
                />
              </label>
              <button className="scanner-camera-button" onClick={() => setCameraMode("recovery-product")} type="button">
                <Camera size={19} />
                Scan product with camera
              </button>
              <label htmlFor="recovery-location-code">
                <span>Scan where the item was found</span>
                <input
                  autoComplete="off"
                  id="recovery-location-code"
                  onChange={(event) => setRecoveryLocationCode(event.target.value)}
                  placeholder="A-03-01"
                  value={recoveryLocationCode}
                />
              </label>
              <button className="scanner-camera-button" onClick={() => setCameraMode("recovery-location")} type="button">
                <Camera size={19} />
                Scan location with camera
              </button>
              <label htmlFor="recovery-quantity">
                <span>Quantity</span>
                <input
                  id="recovery-quantity"
                  inputMode="numeric"
                  min="1"
                  onChange={(event) => setRecoveryQuantity(event.target.value)}
                  type="number"
                  value={recoveryQuantity}
                />
              </label>
              <button
                disabled={!recoveryProductCode.trim() || !recoveryLocationCode.trim() || recoverItem.isPending}
                type="submit"
              >
                {recoverItem.isPending ? "Confirming..." : "Confirm recovered item"}
              </button>
            </form>

            <form className="scanner-workflow-panel" onSubmit={handleReviewShortage}>
              <header>
                <span>6</span>
                <h2>Confirm shortage</h2>
              </header>
              <p className="scanner-helper-text">
                Use this only when the remaining product cannot be found. The confirmed quantity will be removed from
                UNCONFIRMED inventory.
              </p>
              <label htmlFor="shortage-product-code">
                <span>Scan missing product</span>
                <input
                  autoComplete="off"
                  id="shortage-product-code"
                  onChange={(event) => {
                    setShortageProductCode(event.target.value);
                    setShortageReview(false);
                    setShortageOperationId(null);
                  }}
                  placeholder="FILTR-001"
                  value={shortageProductCode}
                />
              </label>
              <button className="scanner-camera-button" onClick={() => setCameraMode("shortage-product")} type="button">
                <Camera size={19} />
                Scan product with camera
              </button>
              <label htmlFor="shortage-quantity">
                <span>Quantity to confirm as missing</span>
                <input
                  id="shortage-quantity"
                  inputMode="numeric"
                  min="1"
                  onChange={(event) => {
                    setShortageQuantity(event.target.value);
                    setShortageReview(false);
                    setShortageOperationId(null);
                  }}
                  type="number"
                  value={shortageQuantity}
                />
              </label>
              {!shortageReview ? (
                <button disabled={!shortageProductCode.trim() || !shortageQuantity.trim()} type="submit">
                  Review shortage confirmation
                </button>
              ) : (
                <div className="scanner-warning-panel">
                  <strong>Confirm shortage</strong>
                  <span>Product: {shortageProductCode}</span>
                  <span>Quantity: {shortageQuantity}</span>
                  <p>
                    This quantity will be removed from UNCONFIRMED inventory and recorded as a confirmed shortage.
                  </p>
                  <button disabled={confirmShortage.isPending} onClick={handleConfirmShortage} type="button">
                    {confirmShortage.isPending ? "Recording..." : "Confirm shortage"}
                  </button>
                  <button
                    className="scanner-secondary-button"
                    onClick={() => {
                      setShortageReview(false);
                      setShortageOperationId(null);
                    }}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </form>
          </>
        )}

      {closeResult && (
        <button className="scanner-confirm-button" onClick={handleResetSession} type="button">
          Scan another pallet
        </button>
      )}

      {!session && !isRestoringSession && !closeResult && (
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
            disabled={closeReceiving.isPending}
            onClick={handleClosePallet}
            type="button"
          >
            {closeReceiving.isPending ? "Closing..." : "Close pallet"}
          </button>
          <button className="scanner-secondary-button" onClick={handleResetSession} type="button">
            Change pallet
          </button>
        </>
      )}
    </>
  );
}
