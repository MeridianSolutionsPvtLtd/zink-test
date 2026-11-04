import React from 'react';
import DashboardLayout from '../layouts/DashboardLayout';
import { SalesAnalyzerModule } from '../components/sales-analyzer/SalesAnalyzerModule';

export default function SalesAnalyzer() {
  return (
    <DashboardLayout>
      <div className="p-4 lg:p-6 max-w-[1700px] mx-auto">
        <SalesAnalyzerModule />
      </div>
    </DashboardLayout>
  );
}
