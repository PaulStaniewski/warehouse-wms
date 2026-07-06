import { useCallback, useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader, type IScannerControls } from "@zxing/browser";

type CameraBarcodeScannerProps = {
  isOpen: boolean;
  onClose: () => void;
  onDetected: (code: string) => void | Promise<void>;
};

function getCameraUnavailableMessage() {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return "Camera access is unavailable in this browser.";
  }

  if (!window.isSecureContext) {
    return "Camera access is unavailable in this browser or connection. Use manual entry, a hardware scanner, or open the app over HTTPS.";
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    return "Camera access is unavailable in this browser. Use manual entry or a hardware scanner.";
  }

  return null;
}

export function CameraBarcodeScanner({ isOpen, onClose, onDetected }: CameraBarcodeScannerProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const hasDetectedRef = useRef(false);
  const mountedRef = useRef(false);
  const onDetectedRef = useRef(onDetected);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [detectedText, setDetectedText] = useState<string | null>(null);

  useEffect(() => {
    onDetectedRef.current = onDetected;
  }, [onDetected]);

  const stopCamera = useCallback(() => {
    controlsRef.current?.stop();
    controlsRef.current = null;

    const video = videoRef.current;
    const stream = video?.srcObject;
    if (stream instanceof MediaStream) {
      stream.getTracks().forEach((track) => track.stop());
    }
    if (video) {
      video.srcObject = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopCamera();
    };
  }, [stopCamera]);

  useEffect(() => {
    if (!isOpen) {
      stopCamera();
      hasDetectedRef.current = false;
      setDetectedText(null);
      setErrorMessage(null);
      return;
    }

    const unavailableMessage = getCameraUnavailableMessage();
    if (unavailableMessage) {
      setErrorMessage(unavailableMessage);
      return;
    }

    hasDetectedRef.current = false;
    setDetectedText(null);
    setErrorMessage(null);

    const reader = new BrowserMultiFormatReader();
    const constraints: MediaStreamConstraints = {
      audio: false,
      video: {
        facingMode: { ideal: "environment" },
      },
    };

    reader
      .decodeFromConstraints(constraints, videoRef.current ?? undefined, (result, _error, controls) => {
        controlsRef.current = controls;
        const text = result?.getText().trim();
        if (!text || hasDetectedRef.current) {
          return;
        }

        hasDetectedRef.current = true;
        setDetectedText("Code detected");
        navigator.vibrate?.(50);
        stopCamera();
        void onDetectedRef.current(text);
      })
      .then((controls) => {
        if (!mountedRef.current || !isOpen) {
          controls.stop();
          return;
        }
        controlsRef.current = controls;
      })
      .catch((error: unknown) => {
        if (!mountedRef.current) {
          return;
        }
        const detail = error instanceof Error && error.message ? ` ${error.message}` : "";
        setErrorMessage(`Camera scanning could not start.${detail}`);
        stopCamera();
      });

    return () => {
      stopCamera();
    };
  }, [isOpen, stopCamera]);

  if (!isOpen) {
    return null;
  }

  return (
    <div className="camera-scanner-overlay" role="dialog" aria-modal="true" aria-label="Scan barcode">
      <section className="camera-scanner-panel">
        <header>
          <h2>Scan barcode</h2>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </header>

        <div className="camera-preview-shell">
          <video ref={videoRef} autoPlay muted playsInline />
          <div className="camera-scan-target" aria-hidden="true" />
        </div>

        <p>{detectedText || "Point the camera at a barcode"}</p>
        {errorMessage && <div className="camera-scanner-error">{errorMessage}</div>}

        <button className="camera-cancel-button" type="button" onClick={onClose}>
          Cancel
        </button>
      </section>
    </div>
  );
}
