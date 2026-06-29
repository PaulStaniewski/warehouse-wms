import { type FormEvent, useState } from "react";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { useScannerProductLookup } from "../api/queries";


function getErrorMessage(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : "Could not load product.";
}

export function ScannerProductLookupPage() {
  const [inputCode, setInputCode] = useState("");
  const [searchCode, setSearchCode] = useState("");
  const lookup = useScannerProductLookup(searchCode);

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
          <p>Product lookup</p>
          <h1>Scan product</h1>
        </div>
        <form className="scanner-scan-panel" onSubmit={handleSubmit}>
          <label htmlFor="product-code">
            <span>Scan product SKU, barcode, or code</span>
            <input
              autoComplete="off"
              autoFocus
              id="product-code"
              onChange={(event) => setInputCode(event.target.value)}
              placeholder="Scan or type code and press Enter"
              value={inputCode}
            />
          </label>
          <button disabled={!inputCode.trim() || lookup.isFetching} type="submit">
            {lookup.isFetching ? "Searching..." : "Search"}
          </button>
        </form>
      </section>

      {lookup.isError && <div className="scanner-message scanner-message--error">{getErrorMessage(lookup.error)}</div>}

      {!searchCode && <div className="state-box">Scan a product to see current stock positions.</div>}

      {lookup.data && (
        <>
          <section className="scanner-result-card">
            <div>
              <span>SKU</span>
              <strong>{lookup.data.product.sku}</strong>
            </div>
            <div>
              <span>Name</span>
              <strong>{lookup.data.product.name}</strong>
            </div>
            <div>
              <span>Barcode</span>
              <strong>{lookup.data.product.barcode ?? "-"}</strong>
            </div>
            <div>
              <span>Unit</span>
              <strong>{lookup.data.product.unit_of_measure}</strong>
            </div>
          </section>

          {lookup.data.inventory_positions.length === 0 ? (
            <div className="state-box">Product exists, but no stock positions were found.</div>
          ) : (
            <section className="scanner-list">
              {lookup.data.inventory_positions.map((item) => (
                <article className="scanner-list-row" key={item.id}>
                  <div>
                    <span>{item.branch_code}</span>
                    <strong>{item.location_code}</strong>
                    {item.location_name && <small>{item.location_name}</small>}
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
