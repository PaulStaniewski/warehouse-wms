import { useEffect, useState } from "react";

import type { ScannerSession } from "../types/api";


const STORAGE_KEY = "warehouse-wms-scanner-session";

export function getStoredScannerSession() {
  const value = window.localStorage.getItem(STORAGE_KEY);
  if (!value) {
    return null;
  }

  try {
    return JSON.parse(value) as ScannerSession;
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function storeScannerSession(session: ScannerSession | null) {
  if (session) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
  window.dispatchEvent(new Event("scanner-session-change"));
}

export function useStoredScannerSession() {
  const [session, setSession] = useState<ScannerSession | null>(() => getStoredScannerSession());

  useEffect(() => {
    function syncSession() {
      setSession(getStoredScannerSession());
    }

    window.addEventListener("scanner-session-change", syncSession);
    window.addEventListener("storage", syncSession);
    return () => {
      window.removeEventListener("scanner-session-change", syncSession);
      window.removeEventListener("storage", syncSession);
    };
  }, []);

  return session;
}
