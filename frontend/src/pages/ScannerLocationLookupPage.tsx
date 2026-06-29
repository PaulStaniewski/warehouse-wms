import { type FormEvent, useState } from "react";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { useScannerLocationContents } from "../api/queries";


function getErrorMessage(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : "Could not load location.";
}

function formatType(value: string) {
  return value.replaceAll("_", " ");
}

export function ScannerLocationLookupPage() {
  const [inputCode, setInputCode] = useState("");
  const [searchCode, setSearchCode] = useState("");
  const lookup = useScannerLocationContents(searchCode);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearchCode(inputCode.trim());
  }

  return (
    <>
      <div className="scanner-links">
        <Link to="/scanner">
          <ArrowLeft size={17} />
          Scanner menu
        </Link>
      </div>

      <section className="scanner-tool-panel">
        <div>
          <p>Location lookup</p>
          <h1>Scan location</h1>
        </div>
        <form className="scanner-scan-panel" onSubmit={handleSubmit}>
          <label htmlFor="location-code">
            <span>Scan location code</span>
            <input
              autoComplete="off"
              autoFocus
              id="location-code"
              onChange={(event) => setInputCode(event.target.value)}
              placeholder="Scan or type location and press Enter"
              value={inputCode}
            />
          </label>
          <button disabled={!inputCode.trim() || lookup.isFetching} type="submit">
            {lookup.isFetching ? "Searching..." : "Search"}
          </button>
        </form>
      </section>

      {lookup.isError && <div className="scanner-message scanner-message--error">{getErrorMessage(lookup.error)}</div>}

      {!searchCode && <div className="state-box">Scan a location to see its contents.</div>}

      {lookup.data && (
        <>
          <section className="scanner-result-card">
            <div>
              <span>Location</span>
              <strong>{lookup.data.location.code}</strong>
            </div>
            <div>
              <span>Name</span>
              <strong>{lookup.data.location.name || "-"}</strong>
            </div>
            <div>
              <span>Branch</span>
              <strong>{lookup.data.location.branch_code}</strong>
            </div>
            <div>
              <span>Type</span>
              <strong>{formatType(lookup.data.location.location_type)}</strong>
            </div>
          </section>

          {lookup.data.inventory_items.length === 0 ? (
            <div className="state-box">Location exists, but no stock was found there.</div>
          ) : (
            <section className="scanner-list">
              {lookup.data.inventory_items.map((item) => (
                <article className="scanner-list-row" key={item.id}>
                  <div>
                    <span>{item.product_sku}</span>
                    <strong>{item.product_name}</strong>
                    <small>{item.product_barcode ?? "No barcode"}</small>
                  </div>
                  <div>
                    <span>On hand</span>
                    <strong>{item.quantity_on_hand}</strong>
                  </div>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </>
  );
}
