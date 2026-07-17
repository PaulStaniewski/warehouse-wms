import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { mockApiClient } from "./apiClientMock";
import {
  anonymousSession,
  authSession,
  branchMembership,
  inventoryExceptionSummary,
  paginated,
  transportOverview,
} from "./fixtures";
import type { BranchMembership } from "../types/api";

type ApiDefaults = {
  authenticated?: boolean;
  memberships?: BranchMembership[];
  username?: string;
};

type RenderWithProvidersOptions = RenderOptions & {
  route?: string;
};

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: Infinity,
        retry: false,
        staleTime: 0,
      },
    },
  });
}

export function setViewport(isMobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      addEventListener: vi.fn(),
      addListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: query.includes("max-width") ? isMobile : !isMobile,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  });
}

export function mockApiDefaults({
  authenticated = true,
  memberships = [branchMembership("leader")],
  username = "GDY_LEADER",
}: ApiDefaults = {}) {
  mockApiClient.get.mockImplementation(async (path: string) => {
    if (path === "/auth/session/") {
      return { data: authenticated ? authSession(username) : anonymousSession() };
    }
    if (path === "/me/branch-memberships/") {
      return { data: authenticated ? memberships : [] };
    }
    if (path.startsWith("/inventory-exceptions/summary/")) {
      return { data: inventoryExceptionSummary() };
    }
    if (path.startsWith("/transport-overview/")) {
      return { data: transportOverview() };
    }
    return { data: paginated([]) };
  });

  mockApiClient.post.mockImplementation(async (path: string) => {
    if (path === "/auth/login/") {
      return { data: authSession(username) };
    }
    if (path === "/auth/logout/") {
      return { data: {} };
    }
    return { data: {} };
  });
}

export function renderWithProviders(ui: ReactElement, options: RenderWithProvidersOptions = {}) {
  const queryClient = createTestQueryClient();
  const route = options.route ?? "/";

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }

  return {
    queryClient,
    ...render(ui, { wrapper: Wrapper, ...options }),
  };
}
