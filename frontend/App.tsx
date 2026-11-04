import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { ThemeProvider } from './src/v2/contexts/ThemeContext';
import { SeasonProvider } from './src/v2/contexts/SeasonContext';
import { AuthProvider } from './src/v2/contexts/AuthContext';
import { ProtectedRoute } from './src/v2/components/ProtectedRoute';

import Login from './src/v2/pages/Login';
import Home from './src/v2/pages/Home';
import SalesDashboard from './src/v2/pages/SalesDashboard';
import ExecutiveDashboard from './src/v2/pages/ExecutiveDashboard';
import InventoryAnalyticsDashboard from './src/v2/pages/InventoryAnalyticsDashboard';
import ProductPerformanceDashboard from './src/v2/pages/ProductPerformanceDashboard';
import FocusDayDashboard from './src/v2/pages/FocusDayDashboard';
import StoreManagersDashboard from './src/v2/pages/StoreManagersDashboard';
import ReplenishmentsRecom from './src/v2/pages/ReplenishmentsRecom';
import TransfersTab from './src/v2/pages/TransfersTab';
import SalesAnalyzer from './src/v2/pages/SalesAnalyzer';

// Secondary / rarely accessed routes lazy loaded
const Stores = lazy(() => import('./src/v2/pages/Stores'));
const Inventory = lazy(() => import('./src/v2/pages/Inventory'));
const SeasonalPlanning = lazy(() => import('./src/v2/pages/SeasonalPlanning'));
const Replenishment = lazy(() => import('./src/v2/pages/Replenishment'));
const Settings = lazy(() => import('./src/v2/pages/Settings'));
const Alerts = lazy(() => import('./src/v2/pages/Alerts'));
const AIPlanning = lazy(() => import('./src/v2/pages/AIPlanning'));
const KPIContract = lazy(() => import('./src/v2/pages/KPIContract'));
const SalesKPI = lazy(() => import('./src/v2/pages/SalesKPI'));
const RetailPerformance = lazy(() => import('./src/v2/pages/RetailPerformance'));
const SavedPlans = lazy(() => import('./src/v2/pages/SavedPlans'));

const PageLoader = () => (
  <div className="flex items-center justify-center h-screen bg-slate-950">
    <div className="flex flex-col items-center gap-3">
      <div className="w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-slate-400 text-sm">Loading...</span>
    </div>
  </div>
);

function Guard({ children }: { children: React.ReactNode }) {
  return <ProtectedRoute>{children}</ProtectedRoute>;
}

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SeasonProvider>
          <Router>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/login" element={<Login />} />
                <Route path="/" element={<Guard><ExecutiveDashboard /></Guard>} />
                <Route path="/upload" element={<Guard><Home /></Guard>} />
                {/* Temporarily hidden — uncomment when needed
                <Route path="/dashboard" element={<Guard><DashboardV2 /></Guard>} />
                */}
                <Route path="/stores" element={<Guard><Stores /></Guard>} />
                <Route path="/inventory" element={<Guard><Inventory /></Guard>} />
                <Route path="/seasonal-planning" element={<Guard><SeasonalPlanning /></Guard>} />
                <Route path="/replenishment" element={<Guard><Replenishment /></Guard>} />
                <Route path="/settings" element={<Guard><Settings /></Guard>} />
                <Route path="/alerts" element={<Guard><Alerts /></Guard>} />
                <Route path="/ai-planning" element={<Guard><AIPlanning /></Guard>} />
                <Route path="/kpi-contract" element={<Guard><KPIContract /></Guard>} />
                <Route path="/sales-kpi" element={<Guard><SalesKPI /></Guard>} />
                <Route path="/retail-performance" element={<Guard><RetailPerformance /></Guard>} />
                <Route path="/saved-plans" element={<Guard><SavedPlans /></Guard>} />
                <Route path="/replenishments" element={<Guard><ReplenishmentsRecom /></Guard>} />
                <Route path="/transfers" element={<Guard><TransfersTab /></Guard>} />
                <Route path="/sales-dashboard" element={<Guard><SalesDashboard /></Guard>} />
                <Route path="/inventory-analytics" element={<Guard><InventoryAnalyticsDashboard /></Guard>} />
                <Route path="/product-performance" element={<Guard><ProductPerformanceDashboard /></Guard>} />
                <Route path="/focus-day-dashboard" element={<Guard><FocusDayDashboard /></Guard>} />
                <Route path="/managers" element={<Guard><StoreManagersDashboard /></Guard>} />
                <Route path="/sales-analyzer" element={<Guard><SalesAnalyzer /></Guard>} />
                <Route path="/executive-dashboard" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </Router>
        </SeasonProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
