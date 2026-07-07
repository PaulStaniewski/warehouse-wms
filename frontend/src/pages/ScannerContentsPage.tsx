import { type FormEvent, useCallback, useRef, useState } from "react";
import axios from "axios";
import { ArrowLeft, Camera } from "lucide-react";
import { Link } from "react-router-dom";

import { useScannerContents } from "../api/queries";
import { CameraBarcodeScanner } from "../components/scanner/CameraBarcodeScanner";
import type { ScannerContentsItem, ScannerContentsResponse } from "../types/api";


function getErrorMessage(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : "Could not load contents.";
}

function formatQuantity(value: number | undefined) {
  if (value === undefined) {
    return "-";
  }

  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(value);
}

function formatObjectType(value: ScannerContentsResponse["object_type"]) {
  if (value === "customer_label") {
    return "CUSTOMER LABEL";
  }
  if (value === "pallet") {
    return "PALLET";
  }

  return value.toUpperCase();
}

function objectLineCount(data: ScannerContentsResponse) {
  const count = data.items.length;
  return `${count} product ${count === 1 ? "line" : "lines"}`;
}

function renderItemDetail(item: ScannerContentsItem, objectType: ScannerContentsResponse["object_type"]) {
  if (objectType === "cart") {
    return (
      <>
        {(item.order_reference || item.customer_name) && (
          <small>{item.order_reference || "-"} · {item.customer_name || "-"}</small>
        )}
        <small>
          Picked {formatQuantity(item.picked_quantity ?? item.quantity)} · Prepared {formatQuantity(item.prepared_quantity)} · Remaining{" "}
          {formatQuantity(item.remaining_quantity)}
        </small>
      </>
    );
  }

  if (objectType === "customer_label") {
    return <small>{formatQuantity(item.quantity)} pcs</small>;
  }

  if (objectType === "pallet") {
    return (
      <small>
        Expected {formatQuantity(item.expected_quantity ?? item.quantity)} · Received {formatQuantity(item.received_quantity)} · Remaining{" "}
        {formatQuantity(item.remaining_quantity)}
        {item.missing_quantity ? ` · Missing ${formatQuantity(item.missing_quantity)}` : ""}
      </small>
    );
  }

  return <small>{formatQuantity(item.quantity)} pcs</small>;
}

export function ScannerContentsPage() {
  const [inputCode, setInputCode] = useState("");
  const [searchCode, setSearchCode] = useState("");
  const [cameraOpen, setCameraOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const lookup = useScannerContents(searchCode);

  const submitCode = useCallback((code: string) => {
    const trimmedCode = code.trim();
    if (!trimmedCode) {
      return;
    }
    setInputCode(trimmedCode);
    setSearchCode(trimmedCode);
  }, []);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitCode(inputCode);
  }

  function handleScanAnother() {
    setInputCode("");
    setSearchCode("");
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  const handleCameraDetected = useCallback((code: string) => {
    setCameraOpen(false);
    submitCode(code);
  }, [submitCode]);

  function handleCameraClose() {
    setCameraOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  return (
    <>
      <div className="scanner-links scanner-links--compact">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
      </div>

      <CameraBarcodeScanner isOpen={cameraOpen} onClose={handleCameraClose} onDetected={handleCameraDetected} />

      {!searchCode && (
        <form className="scanner-workflow-panel" onSubmit={handleSubmit}>
          <header>
            <span>1</span>
            <h2>Contents</h2>
          </header>
          <label htmlFor="contents-code">
            <span>Scan a warehouse code</span>
            <input
              autoComplete="off"
              autoFocus
              id="contents-code"
              onChange={(event) => setInputCode(event.target.value)}
              placeholder="Location, cart, or label code"
              ref={inputRef}
              value={inputCode}
            />
          </label>
          <button disabled={!inputCode.trim() || lookup.isFetching} type="submit">
            {lookup.isFetching ? "Searching..." : "Show contents"}
          </button>
          <button className="scanner-camera-button" onClick={() => setCameraOpen(true)} type="button">
            <Camera size={19} />
            Scan with camera
          </button>
        </form>
      )}

      {lookup.isError && (
        <>
          <div className="scanner-message scanner-message--error">{getErrorMessage(lookup.error)}</div>
          <button className="scanner-confirm-button" onClick={handleScanAnother} type="button">
            Scan another code
          </button>
        </>
      )}

      {lookup.data && (
        <>
          <section className="scanner-contents-header">
            <span>{formatObjectType(lookup.data.object_type)}</span>
            <h1>{lookup.data.code}</h1>
            <p>{lookup.data.description}</p>
            <small>{objectLineCount(lookup.data)}</small>
          </section>

          <section className="scanner-compact-list">
            {lookup.data.items.length === 0 ? (
              <div className="state-box">{lookup.data.title} is empty.</div>
            ) : (
              lookup.data.items.map((item, index) => (
                <article className="scanner-compact-row" key={`${item.sku}-${item.order_reference ?? index}`}>
                  <div>
                    <strong>{item.sku}</strong>
                    <span>{item.name}</span>
                    {renderItemDetail(item, lookup.data.object_type)}
                  </div>
                  <div>
                    <strong>{formatQuantity(item.quantity)}</strong>
                    <small>pcs</small>
                  </div>
                </article>
              ))
            )}
          </section>

          <button className="scanner-confirm-button" onClick={handleScanAnother} type="button">
            Scan another code
          </button>
        </>
      )}
    </>
  );
}
