import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ScannerScanInput, ScannerStatusMessage, ScannerStepIndicator } from "./ScannerUi";

describe("ScannerScanInput", () => {
  it("renders an accessible labelled scan field and autofocuses when requested", async () => {
    render(
      <ScannerScanInput
        autoFocus
        id="scan-code"
        label="Scan product"
        onChange={() => undefined}
        onSubmit={() => undefined}
        value=""
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("Scan product")).toHaveFocus());
  });

  it("trims whitespace and submits once on Enter", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    function ControlledInput() {
      const [value, setValue] = useState("");
      return (
        <ScannerScanInput
          id="scan-code"
          label="Scan product"
          onChange={setValue}
          onSubmit={onSubmit}
          value={value}
        />
      );
    }

    render(<ControlledInput />);
    await user.type(screen.getByLabelText("Scan product"), "  FILTR-001  {Enter}");

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("FILTR-001");
  });

  it("shows pending label and blocks submission while pending", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <ScannerScanInput
        id="scan-code"
        isPending
        label="Scan product"
        onChange={() => undefined}
        onSubmit={onSubmit}
        pendingLabel="Checking..."
        value="FILTR-001"
      />,
    );

    expect(screen.getByRole("button", { name: "Checking..." })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("ScannerStatusMessage", () => {
  it("uses alert semantics for errors and status semantics for success", () => {
    const { rerender } = render(<ScannerStatusMessage type="error">Wrong product</ScannerStatusMessage>);

    expect(screen.getByRole("alert")).toHaveTextContent("Wrong product");

    rerender(<ScannerStatusMessage type="success">Product accepted</ScannerStatusMessage>);
    expect(screen.getByRole("status")).toHaveTextContent("Product accepted");
  });
});

describe("ScannerStepIndicator", () => {
  it("exposes current and completed step states textually", () => {
    render(
      <ScannerStepIndicator
        steps={[
          { label: "Source", isComplete: true },
          { label: "Product", isActive: true },
        ]}
      />,
    );

    expect(screen.getByLabelText("Scanner workflow steps")).toHaveTextContent("Source");
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Current step")).toBeInTheDocument();
  });
});
