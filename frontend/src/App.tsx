import { useEffect, useState } from "react";

import "./App.css";


type HealthState = "loading" | "ok" | "error";

function App() {
  const [backendStatus, setBackendStatus] = useState<HealthState>("loading");

  useEffect(() => {
    let isMounted = true;

    async function loadHealth() {
      try {
        const response = await fetch("/api/health/");
        if (!response.ok) {
          throw new Error("Backend health check failed");
        }

        const data = (await response.json()) as { status?: string };
        if (isMounted) {
          setBackendStatus(data.status === "ok" ? "ok" : "error");
        }
      } catch {
        if (isMounted) {
          setBackendStatus("error");
        }
      }
    }

    loadHealth();

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <main className="app-shell">
      <section className="dashboard">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">Portfolio Warehouse Management System</p>
            <h1>Warehouse WMS</h1>
          </div>
          <span className={`status-pill status-pill--${backendStatus}`}>
            Backend: {backendStatus}
          </span>
        </header>

        <div className="metrics-grid">
          <article>
            <span>Database</span>
            <strong>PostgreSQL</strong>
          </article>
          <article>
            <span>Cache</span>
            <strong>Redis</strong>
          </article>
          <article>
            <span>API</span>
            <strong>{backendStatus === "ok" ? "Healthy" : "Checking"}</strong>
          </article>
        </div>
      </section>
    </main>
  );
}

export default App;
