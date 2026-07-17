import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { mockApiClient, mockGetHealth, mockGetList, resetApiClientMock } from "./apiClientMock";

vi.mock("../api/client", () => ({
  apiClient: mockApiClient,
  getHealth: mockGetHealth,
  getList: mockGetList,
}));

beforeEach(() => {
  resetApiClientMock();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
});
