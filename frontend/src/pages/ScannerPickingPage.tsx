import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { ArrowLeft, Camera } from "lucide-react";
import { Link } from "react-router-dom";

import { useScannerCartWork, useScannerConfirmLocation, useScannerPickingPick } from "../api/queries";
import { useStoredScannerSession } from "../api/scannerSession";
import { DataState } from "../components/DataState";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";


function getErrorMessage(error: unknown, fallback: string) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || fallback : fallback;
}

function formatStatus(status: string) {
  return status.replaceAll("_", " ");
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

export function ScannerPickingPage() {
  const queryClient = useQueryClient();
  const activeSession = useStoredScannerSession();
  const cartWork = useScannerCartWork(activeSession?.id, activeSession?.cart_work_session);
  const confirmLocation = useScannerConfirmLocation();
  const scannerPick = useScannerPickingPick();
  const [workerCode] = useState(activeSession?.worker_code || "DEMO");
  const [locationCode, setLocationCode] = useState("");
  const [productCode, setProductCode] = useState("");
  const [pickQuantity, setPickQuantity] = useState("1");
  const [cameraMode, setCameraMode] = useState<"location" | "product" | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const productInputRef = useRef<HTMLInputElement | null>(null);
  const tasks = cartWork.data?.tasks ?? [];
  const work = cartWork.data?.cart_work_session;
  const instruction = cartWork.data?.current_instruction ?? null;
  const pickingState = cartWork.data?.state ?? "waiting_for_location";
  const totalToPick = tasks.reduce((sum, task) => sum + toNumber(task.quantity_to_pick), 0);
  const totalPicked = tasks.reduce((sum, task) => sum + toNumber(task.quantity_picked), 0);
  const totalPrepared = tasks.reduce((sum, task) => sum + toNumber(task.quantity_prepared), 0);
  const totalRemaining = tasks.reduce((sum, task) => sum + toNumber(task.remaining_quantity), 0);

  const refreshPickingData = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["scanner-cart-work"] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-control-cart-items", activeSession?.id] }),
      queryClient.invalidateQueries({ queryKey: ["scanner-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["route-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "current"] }),
    ]);
  }, [activeSession?.id, queryClient]);

  const submitLocation = useCallback(async (code: string) => {
    if (!work || !instruction) {
      return;
    }

    setMessage(null);
    try {
      const result = await confirmLocation.mutateAsync({ cartWorkSessionId: work.id, locationCode: code });
      setMessage({ type: "success", text: result.message || "Location confirmed." });
      setLocationCode("");
      await refreshPickingData();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not confirm the location.") });
      locationInputRef.current?.focus();
    }
  }, [confirmLocation, instruction, refreshPickingData, work]);

  const submitProduct = useCallback(async (code: string) => {
    if (!work || !instruction || pickingState !== "waiting_for_product") {
      setMessage({ type: "error", text: "Scan the expected location before scanning the product." });
      return;
    }

    setMessage(null);
    try {
      const result = await scannerPick.mutateAsync({
        cartWorkSessionId: work.id,
        code,
        quantity: pickQuantity,
        workerCode,
      });
      setMessage({ type: "success", text: result.message });
      setProductCode("");
      setPickQuantity("1");
      await refreshPickingData();
    } catch (error) {
      setMessage({ type: "error", text: getErrorMessage(error, "Could not pick the product.") });
      productInputRef.current?.focus();
    }
  }, [instruction, pickQuantity, pickingState, refreshPickingData, scannerPick, work, workerCode]);

  function handleConfirmLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitLocation(locationCode);
  }

  function handlePick(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitProduct(productCode);
  }

  const handleCameraDetected = useCallback(async (code: string) => {
    const mode = cameraMode;
    setCameraMode(null);

    if (mode === "location") {
      setLocationCode(code);
      await submitLocation(code);
      return;
    }

    if (mode === "product") {
      setProductCode(code);
      await submitProduct(code);
    }
  }, [cameraMode, submitLocation, submitProduct]);

  function handleCameraClose() {
    const mode = cameraMode;
    setCameraMode(null);
    window.setTimeout(() => {
      if (mode === "location") {
        locationInputRef.current?.focus();
      }
      if (mode === "product") {
        productInputRef.current?.focus();
      }
    }, 0);
  }

  useEffect(() => {
    setProductCode("");
    setLocationCode("");
  }, [instruction?.picking_task_id, pickingState]);

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
        <Link to="/scanner/tasks">Tasks</Link>
        <Link to="/scanner/control">Control</Link>
      </div>

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <CameraBarcodeScanner
        isOpen={cameraMode !== null}
        onClose={handleCameraClose}
        onDetected={handleCameraDetected}
      />

      {!activeSession && (
        <section className="scanner-workflow-panel">
          <header>
            <span>1</span>
          <h2>Picking</h2>
        </header>
        <p>
            No active picking work. Open <Link to="/scanner/tasks">Tasks</Link>, choose a job, and scan a cart.
        </p>
      </section>
      )}

      <DataState isLoading={Boolean(activeSession) && cartWork.isLoading} isError={Boolean(activeSession) && cartWork.isError} error={cartWork.error}>
        {work && (
          <>
            <section className="scanner-header-panel">
              <div>
                <p>Picking</p>
                <h1>
                  Cart <span>{work.cart_code}</span>
                </h1>
                <small>Picking Job #{work.picking_job.id} / {work.picking_job.routes.map((route) => route.route_code).join(", ")}</small>
              </div>
              <dl>
                <div>
                  <dt>Worker</dt>
                  <dd>{workerCode || "-"}</dd>
                </div>
                <div>
                  <dt>Progress</dt>
                  <dd>{work.picking_job.progress_percent}%</dd>
                </div>
              </dl>
            </section>

            <section className="scanner-progress-grid">
              <article>
                <span>To pick</span>
                <strong>{formatQuantity(totalToPick)}</strong>
              </article>
              <article>
                <span>Picked</span>
                <strong>{formatQuantity(totalPicked)}</strong>
              </article>
              <article>
                <span>Prepared</span>
                <strong>{formatQuantity(totalPrepared)}</strong>
              </article>
              <article>
                <span>Remaining</span>
                <strong>{formatQuantity(totalRemaining)}</strong>
              </article>
            </section>

            {!instruction || pickingState === "completed" ? (
              <section className="scanner-workflow-panel">
                <header>
                  <span>Done</span>
                  <h2>Picking completed</h2>
                </header>
                <p>All required picking work for this cart is complete.</p>
                <Link className="scanner-confirm-button" to="/scanner/control">
                  Go to Control
                </Link>
              </section>
            ) : pickingState === "waiting_for_product" ? (
              <section className="scanner-workflow-panel">
                <header>
                  <span>B</span>
                  <h2>Location confirmed</h2>
                </header>
                <section className="scanner-instruction-card">
                  <span>Confirmed location</span>
                  <strong>{instruction.location.code}</strong>
                  <small>{instruction.location.name}</small>
                </section>
                <section className="scanner-result-card">
                  <div>
                    <span>Expected product</span>
                    <strong>{instruction.product.sku}</strong>
                  </div>
                  <div>
                    <span>Name</span>
                    <strong>{instruction.product.name}</strong>
                  </div>
                  <div>
                    <span>Remaining</span>
                    <strong>{formatQuantity(instruction.remaining_quantity)}</strong>
                  </div>
                </section>
                <form className="scanner-scan-form" onSubmit={handlePick}>
                  <label htmlFor="pick-product-code">
                    <span>Scan product barcode or SKU</span>
                    <input
                      autoComplete="off"
                      autoFocus
                      id="pick-product-code"
                      onChange={(event) => setProductCode(event.target.value)}
                      placeholder="Barcode, SKU, or product code"
                      ref={productInputRef}
                      value={productCode}
                    />
                  </label>
                  <label htmlFor="pick-quantity">
                    <span>Quantity</span>
                    <input
                      id="pick-quantity"
                      inputMode="numeric"
                      onChange={(event) => setPickQuantity(event.target.value.replace(/\D/g, ""))}
                      pattern="[0-9]*"
                      placeholder="1"
                      type="text"
                      value={pickQuantity}
                    />
                  </label>
                  <button className="sr-only" disabled={scannerPick.isPending || !productCode.trim() || !pickQuantity.trim()} type="submit">
                    Submit product scan
                  </button>
                  <button className="scanner-camera-button" disabled={scannerPick.isPending} onClick={() => setCameraMode("product")} type="button">
                    <Camera size={19} />
                    Scan with camera
                  </button>
                </form>
              </section>
            ) : (
              <form className="scanner-workflow-panel" onSubmit={handleConfirmLocation}>
                <header>
                  <span>A</span>
                  <h2>Go to location</h2>
                </header>
                <section className="scanner-instruction-card scanner-instruction-card--primary">
                  <span>Next location</span>
                  <strong>{instruction.location.code}</strong>
                  <small>{instruction.location.name}</small>
                </section>
                <section className="scanner-result-card">
                  <div>
                    <span>Next product</span>
                    <strong>{instruction.product.sku}</strong>
                  </div>
                  <div>
                    <span>Name</span>
                    <strong>{instruction.product.name}</strong>
                  </div>
                  <div>
                    <span>Remaining</span>
                    <strong>{formatQuantity(instruction.remaining_quantity)}</strong>
                  </div>
                </section>
                <label htmlFor="pick-location-code">
                  <span>Scan location</span>
                  <input
                    autoComplete="off"
                    autoFocus
                    id="pick-location-code"
                    onChange={(event) => setLocationCode(event.target.value)}
                    placeholder="Location barcode"
                    ref={locationInputRef}
                    value={locationCode}
                  />
                </label>
                <button className="sr-only" disabled={confirmLocation.isPending || !locationCode.trim()} type="submit">
                  Submit location scan
                </button>
                <button className="scanner-camera-button" disabled={confirmLocation.isPending} onClick={() => setCameraMode("location")} type="button">
                  <Camera size={19} />
                  Scan with camera
                </button>
              </form>
            )}

            <section className="picking-list">
              {tasks.map((task) => (
                <article className={`picking-row ${task.status === "completed" ? "picking-row--completed" : ""}`} key={task.id}>
                  <div className="picking-location">
                    <span>Location</span>
                    <strong>{task.source_location_code}</strong>
                    {task.source_location_name && <small>{task.source_location_name}</small>}
                  </div>
                  <div className="picking-product">
                    <span className="mono">{task.product_sku}</span>
                    <h2>{task.product_name}</h2>
                    <p>Order {task.order_reference}</p>
                    <p>Status {formatStatus(task.status)}</p>
                  </div>
                  <div className="picking-quantities">
                    <div>
                      <span>To pick</span>
                      <strong>{formatQuantity(task.quantity_to_pick)}</strong>
                    </div>
                    <div>
                      <span>Picked</span>
                      <strong>{formatQuantity(task.quantity_picked)}</strong>
                    </div>
                    <div>
                      <span>Prepared</span>
                      <strong>{formatQuantity(task.quantity_prepared)}</strong>
                    </div>
                    <div>
                      <span>Remaining</span>
                      <strong>{formatQuantity(task.remaining_quantity)}</strong>
                    </div>
                  </div>
                  <span className={`route-label route-label--${task.status === "picked" || task.status === "completed" ? "selectable" : "neutral"}`}>
                    {formatStatus(task.status)}
                  </span>
                </article>
              ))}
            </section>
          </>
        )}
      </DataState>
    </>
  );
}
