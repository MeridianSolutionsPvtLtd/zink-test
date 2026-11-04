import React, { useEffect, useState } from 'react';
import {
  Store,
  Box,
  Calendar,
  RefreshCw,
  Bell,
  User,
  Users,
  Menu,
  X,
  Sun,
  Moon,
  Layers,
  Package,
  History,
  Home as HomeIcon,
  LogOut,
  BarChart3,
  Shirt,
  CalendarDays,
  Upload,
} from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTheme } from '../contexts/ThemeContext';
import { useFilters } from '../contexts/SeasonContext';
import { useAuth } from '../contexts/AuthContext';
import { FilterDropdown } from '../components/FilterDropdown';
import { MultiFilterDropdown } from '../components/MultiFilterDropdown';
import { DownloadLogsDrawer } from '../components/DownloadLogsDrawer';
import { prefetchDashboardFilterLists } from '../lib/dashboardFilterCache';

const NAV_GROUPS = [
  {
    label: 'Trading',
    items: [
      { path: '/', label: 'Home', icon: HomeIcon },
      { path: '/sales-dashboard', label: 'Sales Overview', icon: BarChart3 },
      { path: '/managers', label: 'Store Managers', icon: Users },
      { path: '/focus-day-dashboard', label: 'Festival Day', icon: CalendarDays },
    ],
  },
  {
    label: 'Floor & stock',
    items: [
      { path: '/inventory-analytics', label: 'Inventory Analytics', icon: Package },
      { path: '/product-performance', label: 'Product Performance', icon: Shirt },
      { path: '/replenishments', label: 'Replenishment', icon: Box },
      { path: '/transfers', label: 'Transfers', icon: RefreshCw },
    ],
  },
  {
    label: 'Data',
    items: [{ path: '/upload', label: 'Data Upload', icon: Upload }],
  },
] as const;

const NAV_ITEMS = NAV_GROUPS.flatMap((group) => [...group.items]);

function SidebarItem({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-200 ${
        active
          ? 'bg-slate-900 text-white shadow-md shadow-slate-900/15 dark:bg-white dark:text-slate-900 dark:shadow-white/10'
          : 'text-slate-600 hover:translate-x-0.5 hover:bg-white hover:text-slate-900 hover:shadow-sm dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-white'
      }`}
    >
      <span
        className={`absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full transition ${
          active ? 'bg-amber-400' : 'bg-transparent group-hover:bg-amber-300/80'
        }`}
        aria-hidden
      />
      <Icon
        className={`h-[18px] w-[18px] shrink-0 ${
          active ? 'text-amber-300 dark:text-amber-600' : 'text-slate-400 group-hover:text-slate-700 dark:group-hover:text-slate-200'
        }`}
      />
      <span className="truncate">{label}</span>
    </button>
  );
}

function Sidebar({
  isOpen,
  setIsOpen,
  onLogsClick,
}: {
  isOpen: boolean;
  setIsOpen: (v: boolean) => void;
  onLogsClick: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { userId, logout } = useAuth();

  const handleNav = (path: string) => {
    navigate(path);
    setIsOpen(false);
  };

  const handleLogout = () => {
    logout();
    setIsOpen(false);
    navigate('/login', { replace: true });
  };

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path);

  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[2px] lg:hidden"
          onClick={() => setIsOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[15.5rem] flex-col border-r border-slate-200/80 bg-[#f7f4ef] shadow-[4px_0_24px_rgba(15,23,42,0.04)] backdrop-blur-md transition-transform duration-200 dark:border-slate-800 dark:bg-slate-950/98 lg:top-14 lg:h-[calc(100vh-3.5rem)] lg:translate-x-0 ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Mobile brand row */}
        <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4 lg:hidden dark:border-slate-800">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-600 to-blue-700 text-white shadow-sm">
            <Box className="h-4 w-4" />
          </div>
          <span className="text-base font-bold tracking-tight text-slate-900 dark:text-white">
            StockPulse
          </span>
          <span className="hidden text-[9px] font-semibold uppercase tracking-widest text-blue-600 lg:inline">Retail BI</span>
          <button
            type="button"
            className="ml-auto rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            onClick={() => setIsOpen(false)}
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <SidebarItem
                    key={item.path}
                    icon={item.icon}
                    label={item.label}
                    active={isActive(item.path)}
                    onClick={() => handleNav(item.path)}
                  />
                ))}
              </div>
            </div>
          ))}
          <div>
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
              Activity
            </p>
            <SidebarItem
              icon={History}
              label="Download Logs"
              onClick={() => {
                onLogsClick();
                setIsOpen(false);
              }}
            />
          </div>
        </nav>

        <div className="border-t border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-center gap-2.5 rounded-xl bg-slate-50 px-3 py-2.5 dark:bg-slate-900">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-500/15 text-blue-600 dark:text-blue-400">
              <User className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-900 dark:text-white">
                {userId || 'User'}
              </div>
              <div className="text-[11px] text-slate-500">Signed in</div>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              title="Logout"
              className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-rose-50 hover:text-rose-500 dark:hover:bg-rose-500/10"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

function TopNavbar({
  onMenuClick,
  headerActions,
}: {
  onMenuClick: () => void;
  headerActions?: React.ReactNode;
}) {
  const { isDark, toggleTheme } = useTheme();
  const {
    season,
    setSeason,
    sku,
    setSku,
    store,
    setStore,
    monthYear,
    setMonthYear,
    category,
    setCategory,
    baselineYears,
    setBaselineYears,
    aiSite,
    setAiSite,
    aiSKU,
    setAiSKU,
    aiDept,
    setAiDept,
    aiMonth,
    setAiMonth,
  } = useFilters();
  const location = useLocation();
  const navigate = useNavigate();

  const handleBaselineYearChange = (years: string[]) => {
    if (years.length > 0) setBaselineYears(years);
  };

  const pageTitle =
    NAV_ITEMS.find((n) =>
      n.path === '/' ? location.pathname === '/' : location.pathname.startsWith(n.path)
    )?.label || 'StockPulse';

  return (
    <header className="fixed inset-x-0 top-0 z-40 h-14 border-b border-slate-200/80 bg-white/92 shadow-[0_1px_0_rgba(15,23,42,0.04)] backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/92">
      <div className="flex h-full items-center gap-3 px-3 sm:px-5">
        <button
          type="button"
          onClick={onMenuClick}
          className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 lg:hidden dark:hover:bg-slate-800"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5" />
        </button>

        <button
          type="button"
          onClick={() => navigate('/')}
          className="flex items-center gap-2.5"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-amber-300 shadow-sm dark:bg-white dark:text-amber-700">
            <Box className="h-4 w-4" />
          </div>
          <div className="hidden text-left sm:block">
            <div className="text-sm font-bold leading-none tracking-tight text-slate-900 dark:text-white">
              StockPulse
            </div>
            <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
              {pageTitle}
            </div>
          </div>
        </button>

        <div className="ml-2 hidden items-center gap-2 rounded-full border border-emerald-200/80 bg-emerald-50 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-800 md:inline-flex dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-300">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
          Floor live
        </div>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          {location.pathname === '/ai-seasonal-planning' && (
            <div className="hidden items-center gap-2 xl:flex">
              <FilterDropdown
                label="Site"
                icon={Store}
                value={aiSite}
                onChange={setAiSite}
                endpoint="/ai-seasonal/sites"
                defaultValue="All Sites"
              />
              <FilterDropdown
                label="SKU"
                icon={Box}
                value={aiSKU}
                onChange={setAiSKU}
                endpoint="/ai-seasonal/skus"
                defaultValue="All SKUs"
              />
              <FilterDropdown
                label="Department"
                icon={Layers}
                value={aiDept}
                onChange={setAiDept}
                endpoint="/ai-seasonal/departments"
                defaultValue="All Departments"
              />
              <FilterDropdown
                label="Month"
                icon={Calendar}
                value={aiMonth}
                onChange={setAiMonth}
                endpoint="/ai-seasonal/months"
                defaultValue="All Months"
              />
              <MultiFilterDropdown
                label="Baseline Years"
                icon={Calendar}
                value={baselineYears}
                onChange={handleBaselineYearChange}
                staticOptions={['2024', '2025']}
                defaultValue="2025"
              />
            </div>
          )}

          {headerActions}

          <button
            type="button"
            onClick={toggleTheme}
            title={isDark ? 'Light mode' : 'Dark mode'}
            className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
          >
            {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>

          <button
            type="button"
            className="relative rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
            aria-label="Notifications"
          >
            <Bell className="h-4 w-4" />
            <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-rose-500 ring-2 ring-white dark:ring-slate-950" />
          </button>
        </div>
      </div>
    </header>
  );
}

export default function DashboardLayout({
  children,
  headerActions,
}: {
  children: React.ReactNode;
  headerActions?: React.ReactNode;
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isLogsOpen, setIsLogsOpen] = useState(false);

  useEffect(() => {
    prefetchDashboardFilterLists();
  }, []);

  return (
    <div className="h-screen overflow-hidden bg-[#efeae3] font-sans text-slate-900 transition-colors duration-200 selection:bg-amber-400/30 dark:bg-[var(--sp-bg)] dark:text-slate-200">
      <TopNavbar onMenuClick={() => setSidebarOpen(true)} headerActions={headerActions} />
      <Sidebar
        isOpen={sidebarOpen}
        setIsOpen={setSidebarOpen}
        onLogsClick={() => setIsLogsOpen(true)}
      />

      <div className="flex h-screen min-w-0 flex-col overflow-x-hidden pt-14 lg:pl-[15.5rem]">
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden p-3 [scrollbar-gutter:stable] lg:p-4">{children}</main>
      </div>

      <DownloadLogsDrawer isOpen={isLogsOpen} onClose={() => setIsLogsOpen(false)} />
    </div>
  );
}
