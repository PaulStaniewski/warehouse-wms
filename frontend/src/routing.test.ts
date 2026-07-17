import { describe, expect, it } from "vitest";

import {
  getDefaultInterfacePath,
  isSafeInternalPath,
  locationToPath,
  resolveIntendedPath,
  SCANNER_HOME_PATH,
  WMS_DASHBOARD_PATH,
} from "./routing";

describe("routing helpers", () => {
  it("chooses WMS for desktop users with both interfaces", () => {
    expect(getDefaultInterfacePath({ canAccessScanner: true, canAccessWms: true }, false)).toBe(WMS_DASHBOARD_PATH);
  });

  it("chooses Scanner for mobile users with both interfaces", () => {
    expect(getDefaultInterfacePath({ canAccessScanner: true, canAccessWms: true }, true)).toBe(SCANNER_HOME_PATH);
  });

  it("keeps explicit interface-only access when only one interface is available", () => {
    expect(getDefaultInterfacePath({ canAccessScanner: false, canAccessWms: true }, true)).toBe(WMS_DASHBOARD_PATH);
    expect(getDefaultInterfacePath({ canAccessScanner: true, canAccessWms: false }, false)).toBe(SCANNER_HOME_PATH);
    expect(getDefaultInterfacePath({ canAccessScanner: false, canAccessWms: false }, false)).toBeNull();
  });

  it("preserves pathname, search, and hash for intended destinations", () => {
    expect(locationToPath({ hash: "#line-4", pathname: "/wms/events/current", search: "?page=2" })).toBe(
      "/wms/events/current?page=2#line-4",
    );
  });

  it("rejects unsafe login redirect targets", () => {
    expect(isSafeInternalPath("/wms/dashboard")).toBe(true);
    expect(isSafeInternalPath("//evil.example/path")).toBe(false);
    expect(isSafeInternalPath("https://evil.example/path")).toBe(false);
    expect(isSafeInternalPath("/login")).toBe(false);
    expect(resolveIntendedPath({ from: "//evil.example/path" })).toBeNull();
  });
});
