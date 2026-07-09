import { Navigate, Route, Routes } from "react-router-dom";

import "./App.css";
import { AppLayout } from "./layout/AppLayout";
import { ArchiveEventsPage } from "./pages/ArchiveEventsPage";
import { CurrentEventsPage } from "./pages/CurrentEventsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DiscrepanciesPage } from "./pages/DiscrepanciesPage";
import { DiscrepancyActionQueuePage } from "./pages/DiscrepancyActionQueuePage";
import { DiscrepancyDetailPage } from "./pages/DiscrepancyDetailPage";
import { DiscrepancyReconciliationDetailPage } from "./pages/DiscrepancyReconciliationDetailPage";
import { DiscrepancyReconciliationsPage } from "./pages/DiscrepancyReconciliationsPage";
import { DiscrepancyReportPage } from "./pages/DiscrepancyReportPage";
import { InventoryPage } from "./pages/InventoryPage";
import { LocationsPage } from "./pages/LocationsPage";
import { OrdersPage } from "./pages/OrdersPage";
import { ProductsPage } from "./pages/ProductsPage";
import { RouteMonitorPage } from "./pages/RouteMonitorPage";
import { RouteArchivePage } from "./pages/RouteArchivePage";
import { RouteDocumentsPage } from "./pages/RouteDocumentsPage";
import { ScannerContentsPage } from "./pages/ScannerContentsPage";
import { ScannerControlPage } from "./pages/ScannerControlPage";
import { ScannerHomePage } from "./pages/ScannerHomePage";
import { ScannerLocationLookupPage } from "./pages/ScannerLocationLookupPage";
import { ScannerPickingPage } from "./pages/ScannerPickingPage";
import { ScannerProformasPage } from "./pages/ScannerProformasPage";
import { ScannerProductLookupPage } from "./pages/ScannerProductLookupPage";
import { ScannerQuickTransferPage } from "./pages/ScannerQuickTransferPage";
import { ScannerReceivingPage } from "./pages/ScannerReceivingPage";
import { ScannerTasksPage } from "./pages/ScannerTasksPage";
import { SourceDiscrepancyReviewDetailPage } from "./pages/SourceDiscrepancyReviewDetailPage";
import { SourceDiscrepancyReviewsPage } from "./pages/SourceDiscrepancyReviewsPage";
import { SourceStockVerificationDetailPage } from "./pages/SourceStockVerificationDetailPage";
import { SourceStockVerificationsPage } from "./pages/SourceStockVerificationsPage";
import { TransitInvestigationDetailPage } from "./pages/TransitInvestigationDetailPage";
import { TransitInvestigationsPage } from "./pages/TransitInvestigationsPage";

function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/wms/dashboard" replace />} />
        <Route path="wms/dashboard" element={<DashboardPage />} />
        <Route path="wms/products" element={<ProductsPage />} />
        <Route path="wms/inventory" element={<InventoryPage />} />
        <Route path="wms/orders" element={<OrdersPage />} />
        <Route path="wms/locations" element={<LocationsPage />} />
        <Route path="wms/routes-monitor" element={<RouteMonitorPage />} />
        <Route path="wms/routes/archive" element={<RouteArchivePage />} />
        <Route path="wms/discrepancy-actions" element={<DiscrepancyActionQueuePage />} />
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
        <Route path="wms/events/current" element={<CurrentEventsPage />} />
        <Route path="wms/events/archive" element={<ArchiveEventsPage />} />
        <Route path="scanner" element={<ScannerHomePage />} />
        <Route path="scanner/proformas" element={<ScannerProformasPage />} />
        <Route path="scanner/tasks" element={<ScannerTasksPage />} />
        <Route path="scanner/picking" element={<ScannerPickingPage />} />
        <Route path="scanner/control" element={<ScannerControlPage />} />
        <Route path="scanner/receiving" element={<ScannerReceivingPage />} />
        <Route path="scanner/routes" element={<Navigate to="/scanner/proformas" replace />} />
        <Route path="scanner/route-runs/:id/picking" element={<Navigate to="/scanner/picking" replace />} />
        <Route path="scanner/route-runs/:id/control" element={<Navigate to="/scanner/control" replace />} />
        <Route path="scanner/product" element={<ScannerProductLookupPage />} />
        <Route path="scanner/contents" element={<ScannerContentsPage />} />
        <Route path="scanner/location" element={<ScannerLocationLookupPage />} />
        <Route path="scanner/quick-transfer" element={<ScannerQuickTransferPage />} />
        <Route path="products" element={<Navigate to="/wms/products" replace />} />
        <Route path="inventory" element={<Navigate to="/wms/inventory" replace />} />
        <Route path="orders" element={<Navigate to="/wms/orders" replace />} />
        <Route path="locations" element={<Navigate to="/wms/locations" replace />} />
        <Route path="routes-monitor" element={<Navigate to="/wms/routes-monitor" replace />} />
        <Route path="*" element={<Navigate to="/wms/dashboard" replace />} />
      </Route>
    </Routes>
  );
}

export default App;
