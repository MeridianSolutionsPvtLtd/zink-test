import React, { useState } from 'react';
import { StoreWiseSohTable } from './StoreWiseSohTable';
import type { StockRow } from './types';
import { LazyMount } from './LazyMount';
import { WarehouseSkuDemandTable } from './WarehouseSkuDemandTable';
import { DispatchPlaybookTable } from './DispatchPlaybookTable';

/** Warehouse tab — shortage analysis only. */
export function WarehouseScopePanels() {
  return (
    <div className="space-y-5">
      <LazyMount minHeight={320}>
        <WarehouseSkuDemandTable />
      </LazyMount>
    </div>
  );
}

/** Store tab — Store-wise SOH Explorer & Dispatch Playbook */
export function StoreScopePanels({
  rows,
  storeSummaries,
  deptSummaries,
  summary,
  serverTotal,
  exportQuery,
  siteId,
  asOfDate,
}: {
  rows: StockRow[];
  storeSummaries?: any[];
  deptSummaries?: any[];
  summary?: any;
  serverTotal?: number;
  exportQuery?: string;
  siteId: string;
  departments?: string[];
  sections?: string[];
  search?: string;
  asOfDate?: string | null;
}) {
  const [selectedStore, setSelectedStore] = useState<string>(siteId !== 'all' ? siteId : '');

  return (
    <div className="space-y-6">
      {/* Store-Wise, Barcode-Wise & Department-Wise Current SOH vs LY */}
      <StoreWiseSohTable
        rows={rows}
        storeSummaries={storeSummaries}
        deptSummaries={deptSummaries}
        summary={summary}
        serverTotal={serverTotal}
        exportQuery={exportQuery}
        asOfDate={asOfDate}
        selectedStore={selectedStore}
        onSelectStore={(store) => setSelectedStore(store === 'all' ? '' : store)}
      />

      {/* Dispatch Playbook — All Stores (Last Year vs This Year Department & Section Winning Dispatch Suggestions) */}
      <LazyMount minHeight={360}>
        <DispatchPlaybookTable highlightSiteId={selectedStore || null} />
      </LazyMount>
    </div>
  );
}
