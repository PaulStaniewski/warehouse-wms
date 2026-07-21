import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Component, lazy, Suspense } from "react";
import type { ComponentType, ErrorInfo, ReactNode } from "react";

import "./App.css";
import { ActiveBranchProvider, useActiveBranch } from "./api/ActiveBranchContext";
import { AuthProvider, useAuth } from "./api/AuthContext";
import { ScannerLayout, WmsLayout } from "./layout/AppLayout";
import { LoginPage } from "./pages/LoginPage";
import {
  getDefaultInterfacePath,
  locationToPath,
  LOGIN_PATH,
  SCANNER_HOME_PATH,
  WMS_DASHBOARD_PATH,
} from "./routing";

function lazyNamed<T extends ComponentType<any>>(
  importer: () => Promise<Record<string, T>>,
  exportName: string,
) {
  return lazy(async () => {
    const module = await importer();
    return { default: module[exportName] };
  });
}

const BranchDetailPage = lazyNamed(() => import("./pages/BranchDetailPage"), "BranchDetailPage");
const BranchesPage = lazyNamed(() => import("./pages/BranchesPage"), "BranchesPage");
const CorrectionActivityReportPage = lazyNamed(() => import("./pages/CorrectionActivityReportPage"), "CorrectionActivityReportPage");
const CycleCountCreatePage = lazyNamed(() => import("./pages/CycleCountCreatePage"), "CycleCountCreatePage");
const CycleCountDetailPage = lazyNamed(() => import("./pages/CycleCountDetailPage"), "CycleCountDetailPage");
const CycleCountReviewQueuePage = lazyNamed(() => import("./pages/CycleCountReviewQueuePage"), "CycleCountReviewQueuePage");
const CycleCountsPage = lazyNamed(() => import("./pages/CycleCountsPage"), "CycleCountsPage");
const DashboardPage = lazyNamed(() => import("./pages/DashboardPage"), "DashboardPage");
const DiscrepanciesPage = lazyNamed(() => import("./pages/DiscrepanciesPage"), "DiscrepanciesPage");
const DiscrepancyActionQueuePage = lazyNamed(() => import("./pages/DiscrepancyActionQueuePage"), "DiscrepancyActionQueuePage");
const DiscrepancyDetailPage = lazyNamed(() => import("./pages/DiscrepancyDetailPage"), "DiscrepancyDetailPage");
const DiscrepancyReconciliationDetailPage = lazyNamed(
  () => import("./pages/DiscrepancyReconciliationDetailPage"),
  "DiscrepancyReconciliationDetailPage",
);
const DiscrepancyReconciliationsPage = lazyNamed(
  () => import("./pages/DiscrepancyReconciliationsPage"),
  "DiscrepancyReconciliationsPage",
);
const DiscrepancyReportPage = lazyNamed(() => import("./pages/DiscrepancyReportPage"), "DiscrepancyReportPage");
const EventDetailPage = lazyNamed(() => import("./pages/EventDetailPage"), "EventDetailPage");
const EventRegisterPage = lazyNamed(() => import("./pages/EventRegisterPage"), "EventRegisterPage");
const InventoryExceptionsPage = lazyNamed(() => import("./pages/InventoryExceptionsPage"), "InventoryExceptionsPage");
const InventoryPage = lazyNamed(() => import("./pages/InventoryPage"), "InventoryPage");
const LocationDetailPage = lazyNamed(() => import("./pages/LocationDetailPage"), "LocationDetailPage");
const LocationsPage = lazyNamed(() => import("./pages/LocationsPage"), "LocationsPage");
const OrdersPage = lazyNamed(() => import("./pages/OrdersPage"), "OrdersPage");
const PickingShortagesPage = lazyNamed(() => import("./pages/PickingShortagesPage"), "PickingShortagesPage");
const ProductsPage = lazyNamed(() => import("./pages/ProductsPage"), "ProductsPage");
const ReplenishmentRequestsPage = lazyNamed(() => import("./pages/ReplenishmentRequestsPage"), "ReplenishmentRequestsPage");
const ReturnDocumentDetailPage = lazyNamed(() => import("./pages/ReturnDocumentDetailPage"), "ReturnDocumentDetailPage");
const ReturnsPage = lazyNamed(() => import("./pages/ReturnsPage"), "ReturnsPage");
const RouteArchivePage = lazyNamed(() => import("./pages/RouteArchivePage"), "RouteArchivePage");
const RouteDocumentsPage = lazyNamed(() => import("./pages/RouteDocumentsPage"), "RouteDocumentsPage");
const RouteMonitorPage = lazyNamed(() => import("./pages/RouteMonitorPage"), "RouteMonitorPage");
const RouteSchedulesPage = lazyNamed(() => import("./pages/RouteSchedulesPage"), "RouteSchedulesPage");
const ScannerContentsPage = lazyNamed(() => import("./pages/ScannerContentsPage"), "ScannerContentsPage");
const ScannerControlPage = lazyNamed(() => import("./pages/ScannerControlPage"), "ScannerControlPage");
const ScannerCycleCountDetailPage = lazyNamed(
  () => import("./pages/ScannerCycleCountDetailPage"),
  "ScannerCycleCountDetailPage",
);
const ScannerCycleCountRecountDetailPage = lazyNamed(
  () => import("./pages/ScannerCycleCountRecountDetailPage"),
  "ScannerCycleCountRecountDetailPage",
);
const ScannerCycleCountRecountsPage = lazyNamed(
  () => import("./pages/ScannerCycleCountRecountsPage"),
  "ScannerCycleCountRecountsPage",
);
const ScannerCycleCountsPage = lazyNamed(() => import("./pages/ScannerCycleCountsPage"), "ScannerCycleCountsPage");
const ScannerHomePage = lazyNamed(() => import("./pages/ScannerHomePage"), "ScannerHomePage");
const ScannerInterBranchArrivalsPage = lazyNamed(
  () => import("./pages/ScannerInterBranchArrivalsPage"),
  "ScannerInterBranchArrivalsPage",
);
const ScannerLocationLookupPage = lazyNamed(() => import("./pages/ScannerLocationLookupPage"), "ScannerLocationLookupPage");
const ScannerPickingPage = lazyNamed(() => import("./pages/ScannerPickingPage"), "ScannerPickingPage");
const ScannerProductLookupPage = lazyNamed(() => import("./pages/ScannerProductLookupPage"), "ScannerProductLookupPage");
const ScannerProformasPage = lazyNamed(() => import("./pages/ScannerProformasPage"), "ScannerProformasPage");
const ScannerQuickTransferPage = lazyNamed(() => import("./pages/ScannerQuickTransferPage"), "ScannerQuickTransferPage");
const ScannerReceivingPage = lazyNamed(() => import("./pages/ScannerReceivingPage"), "ScannerReceivingPage");
const ScannerTasksPage = lazyNamed(() => import("./pages/ScannerTasksPage"), "ScannerTasksPage");
const SalesCorrectionDetailPage = lazyNamed(() => import("./pages/SalesCorrectionDetailPage"), "SalesCorrectionDetailPage");
const SalesCorrectionNewPage = lazyNamed(() => import("./pages/SalesCorrectionNewPage"), "SalesCorrectionNewPage");
const SalesCorrectionsPage = lazyNamed(() => import("./pages/SalesCorrectionsPage"), "SalesCorrectionsPage");
const ShipmentsPage = lazyNamed(() => import("./pages/ShipmentsPage"), "ShipmentsPage");
const SourceDiscrepancyReviewDetailPage = lazyNamed(
  () => import("./pages/SourceDiscrepancyReviewDetailPage"),
  "SourceDiscrepancyReviewDetailPage",
);
const SourceDiscrepancyReviewsPage = lazyNamed(
  () => import("./pages/SourceDiscrepancyReviewsPage"),
  "SourceDiscrepancyReviewsPage",
);
const SourceStockVerificationDetailPage = lazyNamed(
  () => import("./pages/SourceStockVerificationDetailPage"),
  "SourceStockVerificationDetailPage",
);
const SourceStockVerificationsPage = lazyNamed(
  () => import("./pages/SourceStockVerificationsPage"),
  "SourceStockVerificationsPage",
);
const StockAdjustmentCreatePage = lazyNamed(() => import("./pages/StockAdjustmentCreatePage"), "StockAdjustmentCreatePage");
const StockAdjustmentDetailPage = lazyNamed(() => import("./pages/StockAdjustmentDetailPage"), "StockAdjustmentDetailPage");
const StockAdjustmentsPage = lazyNamed(() => import("./pages/StockAdjustmentsPage"), "StockAdjustmentsPage");
const StockTransferDetailPage = lazyNamed(() => import("./pages/StockTransferDetailPage"), "StockTransferDetailPage");
const StockTransfersPage = lazyNamed(() => import("./pages/StockTransfersPage"), "StockTransfersPage");
const TransitInvestigationDetailPage = lazyNamed(
  () => import("./pages/TransitInvestigationDetailPage"),
  "TransitInvestigationDetailPage",
);
const TransitInvestigationsPage = lazyNamed(() => import("./pages/TransitInvestigationsPage"), "TransitInvestigationsPage");
const TransportOverviewPage = lazyNamed(() => import("./pages/TransportOverviewPage"), "TransportOverviewPage");

function PageChunkLoading() {
  return (
    <div className="state-box" role="status">
      Loading page code...
    </div>
  );
}

type PageChunkErrorBoundaryProps = {
  children: ReactNode;
  resetKey: string;
};

type PageChunkErrorBoundaryState = {
  error: Error | null;
  resetKey: string;
};

class PageChunkErrorBoundary extends Component<PageChunkErrorBoundaryProps, PageChunkErrorBoundaryState> {
  state: PageChunkErrorBoundaryState = {
    error: null,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: PageChunkErrorBoundaryProps, state: PageChunkErrorBoundaryState) {
    if (props.resetKey !== state.resetKey) {
      return { error: null, resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Route page failed to load.", error, info);
  }

  retry = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="state-box state-box--error" role="alert">
          <strong>Page code could not be loaded.</strong>
          <p>Check your connection and try again. If this tab was open during a deployment, reload the page.</p>
          <div className="access-denied-actions">
            <button onClick={this.retry} type="button">
              Retry
            </button>
            <button onClick={() => window.location.reload()} type="button">
              Reload page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

function LazyPage({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <PageChunkErrorBoundary resetKey={location.pathname}>
      <Suspense fallback={<PageChunkLoading />}>{children}</Suspense>
    </PageChunkErrorBoundary>
  );
}

function lazyRoute(page: ReactNode) {
  return <LazyPage>{page}</LazyPage>;
}

function useInterfaceAccess() {
  const { isError, isLoading, memberships } = useActiveBranch();

  // The current backend model has branch memberships but no separate interface-level permission.
  const hasBranchAccess = memberships.length > 0;

  return {
    canAccessWms: hasBranchAccess,
    canAccessScanner: hasBranchAccess,
    isError,
    isLoading,
  };
}

function isMobileDefaultViewport() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function AccessDeniedState({
  canAccessScanner,
  canAccessWms,
  detail = "Your account does not have access to this interface.",
}: {
  canAccessScanner: boolean;
  canAccessWms: boolean;
  detail?: string;
}) {
  const auth = useAuth();

  return (
    <main className="access-denied-page">
      <section className="access-denied-panel">
        <span className="login-kicker">Access denied</span>
        <h1>Interface unavailable</h1>
        <p>{detail}</p>
        <div className="access-denied-actions">
          {canAccessWms && <Link to={WMS_DASHBOARD_PATH}>Open WMS</Link>}
          {canAccessScanner && <Link to={SCANNER_HOME_PATH}>Open Scanner</Link>}
          {!canAccessWms && !canAccessScanner && (
            <button onClick={() => void auth.logout()} type="button">
              Logout
            </button>
          )}
        </div>
      </section>
    </main>
  );
}

function AuthenticatedRoute({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.isLoading) {
    return <div className="auth-loading">Checking authentication...</div>;
  }

  if (!auth.isAuthenticated) {
    return <Navigate replace state={{ from: locationToPath(location) }} to={LOGIN_PATH} />;
  }

  return children;
}

function ProtectedWmsRoute({ children }: { children: ReactNode }) {
  const access = useInterfaceAccess();

  if (access.isLoading) {
    return <div className="auth-loading">Loading branch access...</div>;
  }
  if (access.isError) {
    return <AccessDeniedState canAccessScanner={false} canAccessWms={false} detail="Branch access could not be loaded." />;
  }
  if (!access.canAccessWms) {
    return <AccessDeniedState canAccessScanner={access.canAccessScanner} canAccessWms={false} />;
  }

  return children;
}

function ProtectedScannerRoute({ children }: { children: ReactNode }) {
  const access = useInterfaceAccess();

  if (access.isLoading) {
    return <div className="auth-loading">Loading branch access...</div>;
  }
  if (access.isError) {
    return <AccessDeniedState canAccessScanner={false} canAccessWms={false} detail="Branch access could not be loaded." />;
  }
  if (!access.canAccessScanner) {
    return <AccessDeniedState canAccessScanner={false} canAccessWms={access.canAccessWms} />;
  }

  return children;
}

function InterfaceEntryResolver() {
  const auth = useAuth();
  const access = useInterfaceAccess();

  if (auth.isLoading) {
    return <div className="auth-loading">Checking authentication...</div>;
  }
  if (!auth.isAuthenticated) {
    return <Navigate replace state={{ from: "/" }} to={LOGIN_PATH} />;
  }
  if (access.isLoading) {
    return <div className="auth-loading">Loading branch access...</div>;
  }
  if (access.isError) {
    return <AccessDeniedState canAccessScanner={false} canAccessWms={false} detail="Branch access could not be loaded." />;
  }

  const path = getDefaultInterfacePath(access, isMobileDefaultViewport());

  if (!path) {
    return <AccessDeniedState canAccessScanner={false} canAccessWms={false} detail="No WMS or Scanner interface is available for this account." />;
  }

  return <Navigate replace to={path} />;
}

function EventsRedirect({ target }: { target: "current" | "archive" }) {
  const location = useLocation();
  return <Navigate replace to={`/wms/events/${target}${location.search}`} />;
}

function App() {
  return (
    <AuthProvider>
      <ActiveBranchProvider>
        <Routes>
          <Route path="login" element={<LoginPage />} />
          <Route path="/" element={<InterfaceEntryResolver />} />
          <Route
            element={
              <AuthenticatedRoute>
                <ProtectedWmsRoute>
                  <WmsLayout />
                </ProtectedWmsRoute>
              </AuthenticatedRoute>
            }
          >
            <Route path="wms" element={<Navigate to={WMS_DASHBOARD_PATH} replace />} />
            <Route path="wms/dashboard" element={lazyRoute(<DashboardPage />)} />
            <Route path="wms/products" element={lazyRoute(<ProductsPage />)} />
            <Route path="wms/inventory" element={lazyRoute(<InventoryPage />)} />
            <Route path="wms/orders" element={lazyRoute(<OrdersPage />)} />
            <Route path="wms/shipments" element={lazyRoute(<ShipmentsPage />)} />
            <Route path="wms/shipments/:id" element={lazyRoute(<ShipmentsPage />)} />
            <Route path="wms/branches" element={lazyRoute(<BranchesPage />)} />
            <Route path="wms/branches/:id" element={lazyRoute(<BranchDetailPage />)} />
            <Route path="wms/locations" element={lazyRoute(<LocationsPage />)} />
            <Route path="wms/locations/:id" element={lazyRoute(<LocationDetailPage />)} />
            <Route path="wms/stock-transfers" element={lazyRoute(<StockTransfersPage />)} />
            <Route path="wms/stock-transfers/:id" element={lazyRoute(<StockTransferDetailPage />)} />
            <Route path="wms/stock-adjustments" element={lazyRoute(<StockAdjustmentsPage />)} />
            <Route path="wms/stock-adjustments/new" element={lazyRoute(<StockAdjustmentCreatePage />)} />
            <Route path="wms/stock-adjustments/:id" element={lazyRoute(<StockAdjustmentDetailPage />)} />
            <Route path="wms/returns" element={lazyRoute(<ReturnsPage />)} />
            <Route path="wms/returns/:id" element={lazyRoute(<ReturnDocumentDetailPage />)} />
            <Route path="wms/sales-corrections" element={lazyRoute(<SalesCorrectionsPage />)} />
            <Route path="wms/sales-corrections/new" element={lazyRoute(<SalesCorrectionNewPage />)} />
            <Route path="wms/sales-corrections/:id" element={lazyRoute(<SalesCorrectionDetailPage />)} />
            <Route path="wms/reports/correction-activity" element={lazyRoute(<CorrectionActivityReportPage />)} />
            <Route path="wms/cycle-counts" element={lazyRoute(<CycleCountsPage />)} />
            <Route path="wms/cycle-count-review-queue" element={lazyRoute(<CycleCountReviewQueuePage />)} />
            <Route path="wms/cycle-counts/new" element={lazyRoute(<CycleCountCreatePage />)} />
            <Route path="wms/cycle-counts/:id" element={lazyRoute(<CycleCountDetailPage />)} />
            <Route path="wms/transport-overview" element={lazyRoute(<TransportOverviewPage />)} />
            <Route path="wms/routes-monitor" element={lazyRoute(<RouteMonitorPage />)} />
            <Route path="wms/route-schedules" element={lazyRoute(<RouteSchedulesPage />)} />
            <Route path="wms/routes/archive" element={lazyRoute(<RouteArchivePage />)} />
            <Route path="wms/discrepancy-actions" element={lazyRoute(<DiscrepancyActionQueuePage />)} />
            <Route path="wms/replenishment-requests" element={lazyRoute(<ReplenishmentRequestsPage />)} />
            <Route path="wms/inventory-exceptions" element={lazyRoute(<InventoryExceptionsPage />)} />
            <Route path="wms/picking-shortages" element={lazyRoute(<PickingShortagesPage />)} />
            <Route path="wms/discrepancies" element={lazyRoute(<DiscrepanciesPage />)} />
            <Route path="wms/discrepancies/:id" element={lazyRoute(<DiscrepancyDetailPage />)} />
            <Route path="wms/discrepancies/:id/report" element={lazyRoute(<DiscrepancyReportPage />)} />
            <Route path="wms/source-discrepancy-reviews" element={lazyRoute(<SourceDiscrepancyReviewsPage />)} />
            <Route path="wms/source-discrepancy-reviews/:id" element={lazyRoute(<SourceDiscrepancyReviewDetailPage />)} />
            <Route path="wms/discrepancy-reconciliations" element={lazyRoute(<DiscrepancyReconciliationsPage />)} />
            <Route path="wms/discrepancy-reconciliations/:id" element={lazyRoute(<DiscrepancyReconciliationDetailPage />)} />
            <Route path="wms/source-stock-verifications" element={lazyRoute(<SourceStockVerificationsPage />)} />
            <Route path="wms/source-stock-verifications/:id" element={lazyRoute(<SourceStockVerificationDetailPage />)} />
            <Route path="wms/transit-investigations" element={lazyRoute(<TransitInvestigationsPage />)} />
            <Route path="wms/transit-investigations/:id" element={lazyRoute(<TransitInvestigationDetailPage />)} />
            <Route path="wms/route-runs/:id/documents" element={lazyRoute(<RouteDocumentsPage />)} />
            <Route path="wms/current-events" element={<EventsRedirect target="current" />} />
            <Route path="wms/events" element={<EventsRedirect target="current" />} />
            <Route path="wms/events/current" element={lazyRoute(<EventRegisterPage source="current" />)} />
            <Route path="wms/events/archive" element={lazyRoute(<EventRegisterPage source="archive" />)} />
            <Route path="wms/events/:source/:id" element={lazyRoute(<EventDetailPage />)} />
            <Route path="products" element={<Navigate to="/wms/products" replace />} />
            <Route path="inventory" element={<Navigate to="/wms/inventory" replace />} />
            <Route path="orders" element={<Navigate to="/wms/orders" replace />} />
            <Route path="locations" element={<Navigate to="/wms/locations" replace />} />
            <Route path="routes-monitor" element={<Navigate to="/wms/routes-monitor" replace />} />
          </Route>
          <Route
            element={
              <AuthenticatedRoute>
                <ProtectedScannerRoute>
                  <ScannerLayout />
                </ProtectedScannerRoute>
              </AuthenticatedRoute>
            }
          >
            <Route path="scanner" element={lazyRoute(<ScannerHomePage />)} />
            <Route path="scanner/proformas" element={lazyRoute(<ScannerProformasPage />)} />
            <Route path="scanner/tasks" element={lazyRoute(<ScannerTasksPage />)} />
            <Route path="scanner/picking" element={lazyRoute(<ScannerPickingPage />)} />
            <Route path="scanner/control" element={lazyRoute(<ScannerControlPage />)} />
            <Route path="scanner/receiving" element={lazyRoute(<ScannerReceivingPage />)} />
            <Route path="scanner/inter-branch-arrivals" element={lazyRoute(<ScannerInterBranchArrivalsPage />)} />
            <Route path="scanner/cycle-counts" element={lazyRoute(<ScannerCycleCountsPage />)} />
            <Route path="scanner/cycle-counts/:id" element={lazyRoute(<ScannerCycleCountDetailPage />)} />
            <Route path="scanner/cycle-count-recounts" element={lazyRoute(<ScannerCycleCountRecountsPage />)} />
            <Route path="scanner/cycle-count-recounts/:id" element={lazyRoute(<ScannerCycleCountRecountDetailPage />)} />
            <Route path="scanner/routes" element={<Navigate to="/scanner/proformas" replace />} />
            <Route path="scanner/route-runs/:id/picking" element={<Navigate to="/scanner/picking" replace />} />
            <Route path="scanner/route-runs/:id/control" element={<Navigate to="/scanner/control" replace />} />
            <Route path="scanner/product" element={lazyRoute(<ScannerProductLookupPage />)} />
            <Route path="scanner/contents" element={lazyRoute(<ScannerContentsPage />)} />
            <Route path="scanner/location" element={lazyRoute(<ScannerLocationLookupPage />)} />
            <Route path="scanner/quick-transfer" element={lazyRoute(<ScannerQuickTransferPage />)} />
          </Route>
          <Route path="*" element={<Navigate to="/wms/dashboard" replace />} />
        </Routes>
      </ActiveBranchProvider>
    </AuthProvider>
  );
}

export default App;
