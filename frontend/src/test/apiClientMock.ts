import { vi } from "vitest";

import type { PaginatedResponse } from "../types/api";

export const mockApiClient = {
  get: vi.fn(),
  post: vi.fn(),
};

export const mockGetHealth = vi.fn(async () => ({ status: "ok" }));

export const mockGetList = vi.fn(async <T,>(path: string): Promise<PaginatedResponse<T>> => {
  const response = await mockApiClient.get(path);
  const data = response.data;

  if (Array.isArray(data)) {
    return {
      count: data.length,
      next: null,
      previous: null,
      results: data,
    };
  }

  return data;
});

export function resetApiClientMock() {
  mockApiClient.get.mockReset();
  mockApiClient.post.mockReset();
  mockGetHealth.mockClear();
  mockGetList.mockClear();
}
