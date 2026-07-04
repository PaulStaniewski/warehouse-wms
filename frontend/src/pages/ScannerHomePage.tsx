import { type FormEvent, useState } from "react";
import axios from "axios";
import { ArrowRight, ClipboardCheck, ClipboardList, Forklift, MapPin, PackageSearch, ScanLine, Truck } from "lucide-react";
import { Link } from "react-router-dom";

import { storeScannerSession, useStoredScannerSession } from "../api/scannerSession";
import { useScannerSessionEnd, useScannerSessionStart } from "../api/queries";


const menuItems = [
  { label: "Pobranie", description: "Wybierz trasę i pobieraj z półki", to: "/scanner/picking", icon: ClipboardList },
  { label: "Kontrola", description: "Sprawdź pobrane pozycje z etykietą", to: "/scanner/control", icon: ClipboardCheck },
  { label: "Produkt", description: "Lookup SKU or barcode", to: "/scanner/product", icon: PackageSearch },
  { label: "Lokalizacja", description: "Show location contents", to: "/scanner/location", icon: MapPin },
  { label: "Szybkie przeniesienie", description: "Move one item between locations", to: "/scanner/quick-transfer", icon: Forklift },
];

const disabledItems = [
  { label: "Spedycja / MM", icon: Truck },
  { label: "Przyjęcie palety", icon: ScanLine },
  { label: "Inwentaryzacja", icon: ClipboardList },
];

export function ScannerHomePage() {
  const activeSession = useStoredScannerSession();
  const startSession = useScannerSessionStart();
  const endSession = useScannerSessionEnd();
  const [cartCode, setCartCode] = useState("");
  const [workerCode, setWorkerCode] = useState("DEMO");
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  async function handleStartSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);

    try {
      const result = await startSession.mutateAsync({ cartCode, workerCode });
      storeScannerSession(result.session);
      setCartCode("");
      setMessage({ type: "success", text: `Aktywny wózek: ${result.session.cart_code}` });
    } catch (error) {
      const text = axios.isAxiosError(error)
        ? error.response?.data?.detail || "Nie można rozpocząć sesji wózka."
        : "Nie można rozpocząć sesji wózka.";
      setMessage({ type: "error", text });
    }
  }

  async function handleEndSession() {
    if (!activeSession) {
      return;
    }

    try {
      await endSession.mutateAsync({ sessionId: activeSession.id });
      storeScannerSession(null);
      setMessage({ type: "success", text: "Wózek zwolniony." });
    } catch (error) {
      const text = axios.isAxiosError(error)
        ? error.response?.data?.detail || "Nie można zwolnić wózka."
        : "Nie można zwolnić wózka.";
      setMessage({ type: "error", text });
    }
  }

  return (
    <>
      <section className="scanner-home-header">
        <p>Scanner module</p>
        <h1>Warehouse scanner</h1>
      </section>

      {message && <div className={`scanner-message scanner-message--${message.type}`}>{message.text}</div>}

      <section className="scanner-cart-panel">
        <div>
          <span>Wózek</span>
          <strong>{activeSession ? activeSession.cart_code : "Brak aktywnego wózka"}</strong>
          {activeSession?.worker_code && <small>Worker: {activeSession.worker_code}</small>}
        </div>
        <form onSubmit={handleStartSession}>
          <label htmlFor="scanner-cart-code">
            <span>Zeskanuj wózek</span>
            <input
              autoComplete="off"
              autoFocus={!activeSession}
              id="scanner-cart-code"
              onChange={(event) => setCartCode(event.target.value)}
              placeholder="WOZEK-01"
              value={cartCode}
            />
          </label>
          <label htmlFor="scanner-worker-code">
            <span>Worker</span>
            <input
              autoComplete="off"
              id="scanner-worker-code"
              onChange={(event) => setWorkerCode(event.target.value)}
              value={workerCode}
            />
          </label>
          <button disabled={!cartCode.trim() || startSession.isPending} type="submit">
            {startSession.isPending ? "Start..." : "Ustaw wózek"}
          </button>
        </form>
        <button disabled={!activeSession || endSession.isPending} onClick={handleEndSession} type="button">
          Zwolnij wózek
        </button>
      </section>

      <section className="scanner-menu-grid">
        {menuItems.map((item) => {
          const Icon = item.icon;

          return (
            <Link className="scanner-menu-card" key={item.to} to={item.to}>
              <Icon size={32} />
              <div>
                <strong>{item.label}</strong>
                <span>{item.description}</span>
              </div>
              <ArrowRight size={24} />
            </Link>
          );
        })}

        {disabledItems.map((item) => {
          const Icon = item.icon;

          return (
            <article className="scanner-menu-card scanner-menu-card--disabled" key={item.label}>
              <Icon size={32} />
              <div>
                <strong>{item.label}</strong>
                <span>Coming soon</span>
              </div>
            </article>
          );
        })}
      </section>
    </>
  );
}
