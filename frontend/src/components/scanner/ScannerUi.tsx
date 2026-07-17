import { type FormEvent, type ReactNode, useEffect, useRef } from "react";

type ScannerScanInputProps = {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  placeholder?: string;
  helperText?: string;
  buttonLabel?: string;
  pendingLabel?: string;
  disabled?: boolean;
  isPending?: boolean;
  autoFocus?: boolean;
  inputMode?: "none" | "text" | "decimal" | "numeric" | "tel" | "search" | "email" | "url";
};

type ScannerStatusMessageProps = {
  type: "success" | "error" | "warning" | "info";
  children: ReactNode;
};

type ScannerStep = {
  label: string;
  isComplete?: boolean;
  isActive?: boolean;
};

export function ScannerScanInput({
  autoFocus = false,
  buttonLabel = "Confirm",
  disabled = false,
  helperText,
  id,
  inputMode = "text",
  isPending = false,
  label,
  onChange,
  onSubmit,
  pendingLabel = "Working...",
  placeholder,
  value,
}: ScannerScanInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!autoFocus || disabled || isPending) {
      return;
    }
    const handle = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(handle);
  }, [autoFocus, disabled, isPending]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedValue = value.trim();
    if (!trimmedValue || disabled || isPending) {
      return;
    }
    onSubmit(trimmedValue);
  }

  return (
    <form className="scanner-scan-panel scanner-scan-panel--shared" onSubmit={handleSubmit}>
      <label htmlFor={id}>
        <span>{label}</span>
        <input
          autoComplete="off"
          disabled={disabled}
          id={id}
          inputMode={inputMode}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          ref={inputRef}
          value={value}
        />
        {helperText && <small>{helperText}</small>}
      </label>
      <button disabled={!value.trim() || disabled || isPending} type="submit">
        {isPending ? pendingLabel : buttonLabel}
      </button>
    </form>
  );
}

export function ScannerStatusMessage({ children, type }: ScannerStatusMessageProps) {
  return (
    <div className={`scanner-message scanner-message--${type}`} role={type === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function ScannerStepIndicator({ steps }: { steps: ScannerStep[] }) {
  return (
    <ol className="scanner-step-indicator" aria-label="Scanner workflow steps">
      {steps.map((step, index) => (
        <li
          className={[
            "scanner-step-indicator__item",
            step.isActive ? "is-active" : "",
            step.isComplete ? "is-complete" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          key={step.label}
        >
          <span>{index + 1}</span>
          <strong>{step.label}</strong>
          {step.isActive && <em className="sr-only">Current step</em>}
          {step.isComplete && <em className="sr-only">Completed</em>}
        </li>
      ))}
    </ol>
  );
}
