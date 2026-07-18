import axios from "axios";

import type { HealthResponse, PaginatedResponse } from "../types/api";


export const apiClient = axios.create({
  baseURL: "/api",
  withCredentials: true,
  xsrfCookieName: "csrftoken",
  xsrfHeaderName: "X-CSRFToken",
  headers: {
    Accept: "application/json",
  },
});

export async function getHealth() {
  const response = await apiClient.get<HealthResponse>("/health/");
  return response.data;
}

export async function getList<T>(path: string) {
  const response = await apiClient.get<PaginatedResponse<T> | T[]>(path);

  if (Array.isArray(response.data)) {
    return {
      count: response.data.length,
      next: null,
      previous: null,
      results: response.data,
    };
  }

  return response.data;
}
