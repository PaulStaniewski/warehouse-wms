import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import "./App.css";
import { ActiveBranchProvider, useActiveBranch } from "./api/ActiveBranchContext";
import { AuthProvider, useAuth } from "./api/AuthContext";
import { ScannerLayout, WmsLayout } from "./layout/AppLayout";
import { BranchDetailPage } from "./pages/BranchDetailPage";
import { BranchesPage } from "./pages/BranchesPage";
import { CycleCountCreatePage } from "./pages/CycleCountCreatePage";
import { CycleCountDetailPage } from "./pages/CycleCountDetailPage";
import { CycleCountReviewQueuePage } from "./pages/CycleCountReviewQueuePage";
import { CycleCountsPage } from "./pages/CycleCountsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DiscrepanciesPage } from "./pages/DiscrepanciesPage";
import { DiscrepancyActionQueuePage } from "./pages/DiscrepancyActionQueuePage";
import { DiscrepancyDetailPage } from "./pages/DiscrepancyDetailPage";
import { DiscrepancyReconciliationDetailPage } from "./pages/DiscrepancyReconciliationDetailPage";
import { DiscrepancyReconciliationsPage } from "./pages/DiscrepancyReconciliationsPage";
import { DiscrepancyReportPage } from "./pages/DiscrepancyReportPage";
import { EventDetailPage } from "./pages/EventDetailPage";
import { EventRegisterPage } from "./pages/EventRegisterPage";
import { InventoryExceptionsPage } from "./pages/InventoryExceptionsPage";
import { InventoryPage } from "./pages/InventoryPage";
import { LocationsPage } from "./pages/LocationsPage";
import { LocationDetailPage } from "./pages/LocationDetailPage";
import { LoginPage } from "./pages/LoginPage";
import { OrdersPage } from "./pages/OrdersPage";
import { PickingShortagesPage } from "./pages/PickingShortagesPage";
import { ProductsPage } from "./pages/ProductsPage";
import { ReplenishmentRequestsPage } from "./pages/ReplenishmentRequestsPage";
import { RouteMonitorPage } from "./pages/RouteMonitorPage";
import { RouteArchivePage } from "./pages/RouteArchivePage";
import { RouteDocumentsPage } from "./pages/RouteDocumentsPage";
import { ScannerContentsPage } from "./pages/ScannerContentsPage";
import { ScannerControlPage } from "./pages/ScannerControlPage";
import { ScannerCycleCountDetailPage } from "./pages/ScannerCycleCountDetailPage";
import { ScannerCycleCountRecountDetailPage } from "./pages/ScannerCycleCountRecountDetailPage";
import { ScannerCycleCountRecountsPage } from "./pages/ScannerCycleCountRecountsPage";
import { ScannerCycleCountsPage } from "./pages/ScannerCycleCountsPage";
import { ScannerHomePage } from "./pages/ScannerHomePage";
import { ScannerLocationLookupPage } from "./pages/ScannerLocationLookupPage";
import { ScannerPickingPage } from "./pages/ScannerPickingPage";
import { ScannerProformasPage } from "./pages/ScannerProformasPage";
import { ScannerProductLookupPage } from "./pages/ScannerProductLookupPage";
import { ScannerQuickTransferPage } from "./pages/ScannerQuickTransferPage";
import { ScannerReceivingPage } from "./pages/ScannerReceivingPage";
import { ScannerInterBranchArrivalsPage } from "./pages/ScannerInterBranchArrivalsPage";
import { ScannerTasksPage } from "./pages/ScannerTasksPage";
import { SourceDiscrepancyReviewDetailPage } from "./pages/SourceDiscrepancyReviewDetailPage";
import { SourceDiscrepancyReviewsPage } from "./pages/SourceDiscrepancyReviewsPage";
import { SourceStockVerificationDetailPage } from "./pages/SourceStockVerificationDetailPage";
import { SourceStockVerificationsPage } from "./pages/SourceStockVerificationsPage";
import { StockAdjustmentCreatePage } from "./pages/StockAdjustmentCreatePage";
import { StockAdjustmentDetailPage } from "./pages/StockAdjustmentDetailPage";
import { StockAdjustmentsPage } from "./pages/StockAdjustmentsPage";
import { StockTransferDetailPage } from "./pages/StockTransferDetailPage";
import { StockTransfersPage } from "./pages/StockTransfersPage";
import { TransitInvestigationDetailPage } from "./pages/TransitInvestigationDetailPage";
import { TransitInvestigationsPage } from "./pages/TransitInvestigationsPage";
import { TransportOverviewPage } from "./pages/TransportOverviewPage";
import {
  getDefaultInterfacePath,
  locationToPath,
  LOGIN_PATH,
  SCANNER_HOME_PATH,
  WMS_DASHBOARD_PATH,
} from "./routing";

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
            <Route path="wms/dashboard" element={<DashboardPage />} />
            <Route path="wms/products" element={<ProductsPage />} />
            <Route path="wms/inventory" element={<InventoryPage />} />
            <Route path="wms/orders" element={<OrdersPage />} />
            <Route path="wms/branches" element={<BranchesPage />} />
            <Route path="wms/branches/:id" element={<BranchDetailPage />} />
            <Route path="wms/locations" element={<LocationsPage />} />
            <Route path="wms/locations/:id" element={<LocationDetailPage />} />
            <Route path="wms/stock-transfers" element={<StockTransfersPage />} />
            <Route path="wms/stock-transfers/:id" element={<StockTransferDetailPage />} />
            <Route path="wms/stock-adjustments" element={<StockAdjustmentsPage />} />
            <Route path="wms/stock-adjustments/new" element={<StockAdjustmentCreatePage />} />
            <Route path="wms/stock-adjustments/:id" element={<StockAdjustmentDetailPage />} />
            <Route path="wms/cycle-counts" element={<CycleCountsPage />} />
            <Route path="wms/cycle-count-review-queue" element={<CycleCountReviewQueuePage />} />
            <Route path="wms/cycle-counts/new" element={<CycleCountCreatePage />} />
            <Route path="wms/cycle-counts/:id" element={<CycleCountDetailPage />} />
            <Route path="wms/transport-overview" element={<TransportOverviewPage />} />
            <Route path="wms/routes-monitor" element={<RouteMonitorPage />} />
            <Route path="wms/routes/archive" element={<RouteArchivePage />} />
            <Route path="wms/discrepancy-actions" element={<DiscrepancyActionQueuePage />} />
            <Route path="wms/replenishment-requests" element={<ReplenishmentRequestsPage />} />
            <Route path="wms/inventory-exceptions" element={<InventoryExceptionsPage />} />
            <Route path="wms/picking-shortages" element={<PickingShortagesPage />} />
            <Route path="wms/discrepancies" element={<DiscrepanciesPage />} />
            <Route path="wms/discrepancies/:id" element={<DiscrepancyDetailPage />} />
            <Route path="wms/discrepancies/:id/report" element={<DiscrepancyReportPage />} />
            <Route path="wms/source-discrepancy-reviews" element={<SourceDiscrepancyReviewsPage />} />
            <Route path="wms/source-discrepancy-reviews/:id" element={<SourceDiscrepancyReviewDetailPage />} />
            <Route path="wms/discrepancy-reconciliations" element={<DiscrepancyReconciliationsPage />} />
            <Route path="wms/discrepancy-reconciliations/:id" element={<DiscrepancyReconciliationDetailPage />} />
            <Route path="wms/source-stock-verifications" element={<SourceStockVerificationsPage />} />
            <Route path="wms/source-stock-verifications/:id" element={<SourceStockVerificationDetailPage />} />
            <Route path="wms/transit-investigations" element={<TransitInvestigationsPage />} />
            <Route path="wms/transit-investigations/:id" element={<TransitInvestigationDetailPage />} />
            <Route path="wms/route-runs/:id/documents" element={<RouteDocumentsPage />} />
            <Route path="wms/current-events" element={<EventsRedirect target="current" />} />
            <Route path="wms/events" element={<EventsRedirect target="current" />} />
            <Route path="wms/events/current" element={<EventRegisterPage source="current" />} />
            <Route path="wms/events/archive" element={<EventRegisterPage source="archive" />} />
            <Route path="wms/events/:source/:id" element={<EventDetailPage />} />
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
            <Route path="scanner" element={<ScannerHomePage />} />
            <Route path="scanner/proformas" element={<ScannerProformasPage />} />
            <Route path="scanner/tasks" element={<ScannerTasksPage />} />
            <Route path="scanner/picking" element={<ScannerPickingPage />} />
            <Route path="scanner/control" element={<ScannerControlPage />} />
            <Route path="scanner/receiving" element={<ScannerReceivingPage />} />
            <Route path="scanner/inter-branch-arrivals" element={<ScannerInterBranchArrivalsPage />} />
            <Route path="scanner/cycle-counts" element={<ScannerCycleCountsPage />} />
            <Route path="scanner/cycle-counts/:id" element={<ScannerCycleCountDetailPage />} />
            <Route path="scanner/cycle-count-recounts" element={<ScannerCycleCountRecountsPage />} />
            <Route path="scanner/cycle-count-recounts/:id" element={<ScannerCycleCountRecountDetailPage />} />
            <Route path="scanner/routes" element={<Navigate to="/scanner/proformas" replace />} />
            <Route path="scanner/route-runs/:id/picking" element={<Navigate to="/scanner/picking" replace />} />
            <Route path="scanner/route-runs/:id/control" element={<Navigate to="/scanner/control" replace />} />
            <Route path="scanner/product" element={<ScannerProductLookupPage />} />
            <Route path="scanner/contents" element={<ScannerContentsPage />} />
            <Route path="scanner/location" element={<ScannerLocationLookupPage />} />
            <Route path="scanner/quick-transfer" element={<ScannerQuickTransferPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/wms/dashboard" replace />} />
        </Routes>
      </ActiveBranchProvider>
    </AuthProvider>
  );
}

export default App;
