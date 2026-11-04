import React from 'react';

export interface Metric {
  label: string;
  value: string;
  trend: number;
  trendLabel: string; // e.g., "vs last month"
  status: 'positive' | 'negative' | 'neutral' | 'warning';
  icon?: React.ReactNode;
}

export interface Store {
  id: string;
  name: string;
  region: string;
  sellThrough: number;
  inventoryValue: number;
  idleInventory?: number;
  idleInventoryValue?: number;
  discountRate: number;
  status: 'Optimal' | 'Needs Attention' | 'Critical';
  trend: number;
}

export interface Alert {
  id: string;
  type: 'Low Stock' | 'Slow-Moving' | 'Declining' | 'Overstock';
  title: string;
  description: string;
  timeAgo: string;
  severity: 'high' | 'medium' | 'low';
  affectedStores?: number;
  affectedSKUs?: number;
  impactValue?: number;
  metrics?: { label: string; value: string; status: string }[];
}

export interface RegionData {
  name: string;
  storeCount: number;
  performance: number; // percentage
}

export enum View {
  DASHBOARD = 'Dashboard',
  DATA_INSIGHTS = 'Data Insights',
  STORES = 'Stores',
  INVENTORY = 'Inventory',
  ANALYTICS = 'Analytics',
  SELL_THROUGH = 'Sell-Through',
  SEASONAL = 'Seasonal Planning',
  REPLENISHMENT = 'Replenishment',
  ALERTS = 'Alerts',
  SETTINGS = 'Settings'
}