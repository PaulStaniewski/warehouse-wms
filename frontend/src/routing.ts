import type { Location } from "react-router-dom";

export const WMS_DASHBOARD_PATH = "/wms/dashboard";
export const SCANNER_HOME_PATH = "/scanner";
export const LOGIN_PATH = "/login";

export type InterfaceAccess = {
  canAccessWms: boolean;
  canAccessScanner: boolean;
};

export type LoginLocationState = {
  from?: string;
};

export function isSafeInternalPath(path: unknown) {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") && !path.startsWith("/login");
}

export function locationToPath(location: Pick<Location, "pathname" | "search" | "hash">) {
  return `${location.pathname}${location.search}${location.hash}`;
}

export function getDefaultInterfacePath(access: InterfaceAccess, isMobileViewport: boolean) {
  if (access.canAccessScanner && !access.canAccessWms) {
    return SCANNER_HOME_PATH;
  }
  if (access.canAccessWms && !access.canAccessScanner) {
    return WMS_DASHBOARD_PATH;
  }
  if (access.canAccessWms && access.canAccessScanner) {
    return isMobileViewport ? SCANNER_HOME_PATH : WMS_DASHBOARD_PATH;
  }
  return null;
}

export function resolveIntendedPath(state: unknown) {
  if (!state || typeof state !== "object" || !("from" in state)) {
    return null;
  }
  const candidate = (state as LoginLocationState).from;
  return isSafeInternalPath(candidate) ? candidate : null;
}
