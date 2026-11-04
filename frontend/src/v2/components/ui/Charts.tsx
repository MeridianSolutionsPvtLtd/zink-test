import React, { useMemo, useState, useEffect } from 'react';
import { api } from '../../../../lib/api';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  BarChart,
  Bar,
  Legend
} from 'recharts';

// Region-specific sell-through trends (Indian regions)
const regionalSellThroughData: Record<string, number[]> = {
  'North': [68, 70, 73, 72, 75, 74, 77, 76, 79, 81],
  'South': [64, 66, 69, 68, 71, 70, 73, 72, 74, 75],
  'Central': [58, 60, 63, 61, 65, 63, 66, 64, 67, 68],
  'Northeast': [66, 68, 71, 70, 73, 72, 75, 74, 76, 78],
  'West': [72, 74, 77, 76, 79, 78, 81, 80, 82, 84],
  'East': [52, 54, 57, 55, 58, 56, 59, 57, 60, 61],
  'Tier-2 Cities': [60, 62, 65, 64, 67, 66, 69, 68, 70, 72]
};

// Region-specific discount data for Indian categories
const regionalDiscountData: Record<string, Array<{category: string, fullPrice: number, discounted: number}>> = {
  'North': [
    { category: 'Ethnic Wear', fullPrice: 68, discounted: 32 },
    { category: 'Kurtas', fullPrice: 75, discounted: 25 },
    { category: 'Bottoms', fullPrice: 63, discounted: 37 },
    { category: 'Winter Wear', fullPrice: 58, discounted: 42 },
    { category: 'Accessories', fullPrice: 88, discounted: 12 },
    { category: 'Footwear', fullPrice: 62, discounted: 38 },
  ],
  'South': [
    { category: 'Ethnic Wear', fullPrice: 62, discounted: 38 },
    { category: 'Kurtas', fullPrice: 70, discounted: 30 },
    { category: 'Bottoms', fullPrice: 58, discounted: 42 },
    { category: 'Winter Wear', fullPrice: 52, discounted: 48 },
    { category: 'Accessories', fullPrice: 82, discounted: 18 },
    { category: 'Footwear', fullPrice: 55, discounted: 45 },
  ],
  'Central': [
    { category: 'Ethnic Wear', fullPrice: 55, discounted: 45 },
    { category: 'Kurtas', fullPrice: 62, discounted: 38 },
    { category: 'Bottoms', fullPrice: 50, discounted: 50 },
    { category: 'Winter Wear', fullPrice: 45, discounted: 55 },
    { category: 'Accessories', fullPrice: 75, discounted: 25 },
    { category: 'Footwear', fullPrice: 48, discounted: 52 },
  ],
  'Northeast': [
    { category: 'Ethnic Wear', fullPrice: 64, discounted: 36 },
    { category: 'Kurtas', fullPrice: 72, discounted: 28 },
    { category: 'Bottoms', fullPrice: 60, discounted: 40 },
    { category: 'Winter Wear', fullPrice: 54, discounted: 46 },
    { category: 'Accessories', fullPrice: 85, discounted: 15 },
    { category: 'Footwear', fullPrice: 58, discounted: 42 },
  ],
  'West': [
    { category: 'Ethnic Wear', fullPrice: 72, discounted: 28 },
    { category: 'Kurtas', fullPrice: 78, discounted: 22 },
    { category: 'Bottoms', fullPrice: 68, discounted: 32 },
    { category: 'Winter Wear', fullPrice: 62, discounted: 38 },
    { category: 'Accessories', fullPrice: 90, discounted: 10 },
    { category: 'Footwear', fullPrice: 65, discounted: 35 },
  ],
  'East': [
    { category: 'Ethnic Wear', fullPrice: 48, discounted: 52 },
    { category: 'Kurtas', fullPrice: 55, discounted: 45 },
    { category: 'Bottoms', fullPrice: 45, discounted: 55 },
    { category: 'Winter Wear', fullPrice: 40, discounted: 60 },
    { category: 'Accessories', fullPrice: 70, discounted: 30 },
    { category: 'Footwear', fullPrice: 42, discounted: 58 },
  ],
  'Tier-2 Cities': [
    { category: 'Ethnic Wear', fullPrice: 58, discounted: 42 },
    { category: 'Kurtas', fullPrice: 65, discounted: 35 },
    { category: 'Bottoms', fullPrice: 55, discounted: 45 },
    { category: 'Winter Wear', fullPrice: 50, discounted: 50 },
    { category: 'Accessories', fullPrice: 78, discounted: 22 },
    { category: 'Footwear', fullPrice: 52, discounted: 48 },
  ]
};

interface ChartProps {
  selectedRegion: string;
  selectedDays?: number;
}

export const SellThroughChart: React.FC<ChartProps> = ({ selectedRegion, selectedDays = 30 }) => {
  const [apiData, setApiData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.sellthrough.trend(selectedRegion, selectedDays)
      .then((res) => {
        const formatted = (res.months || []).map((m: any) => ({
          month: formatMonthYear(m.month), // "2024-11" -> "Nov 2024"
          value: m.sellThroughPct || 0,
        }));
        setApiData(formatted);
      })
      .catch(() => setApiData([]))
      .finally(() => setLoading(false));
  }, [selectedRegion, selectedDays]);

  // Format YYYY-MM to "Mon YYYY"
  function formatMonthYear(ym: string): string {
    if (!ym || !ym.includes('-')) return ym;
    const [year, month] = ym.split('-');
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const monthName = months[parseInt(month, 10) - 1] || month;
    return `${monthName} ${year}`;
  }

  return (
    <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm h-[350px]">
      <div className="mb-6">
        <h3 className="text-lg font-bold text-slate-900">Sell-Through Rate Trend</h3>
        <p className="text-sm text-slate-500">Monthly performance vs target</p>
        <div className="mt-2 inline-flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5">
          <span className="text-xs font-medium text-emerald-900">Formula:</span>
          <code className="text-xs font-mono text-emerald-700 bg-white px-2 py-0.5 rounded border border-emerald-200">
            (Sales Units / Purchased Units) × 100
          </code>
        </div>
      </div>
      {loading ? (
        <div className="flex items-center justify-center h-[260px] text-slate-400">Loading...</div>
      ) : apiData.length === 0 ? (
        <div className="flex items-center justify-center h-[260px] text-slate-400">No data available</div>
      ) : (
        <div style={{ width: '100%', height: 260, minHeight: 260, minWidth: 0 }}>
          <ResponsiveContainer width="100%" height={260} minWidth={0}>
          <LineChart data={apiData}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
            <XAxis 
              dataKey="month" 
              axisLine={false} 
              tickLine={false} 
              tick={{ fill: '#64748b', fontSize: 11 }} 
              dy={10}
              angle={-45}
              textAnchor="end"
              height={60}
            />
            <YAxis 
              axisLine={false} 
              tickLine={false} 
              tick={{ fill: '#64748b', fontSize: 12 }} 
              tickFormatter={(value) => `${value}%`}
            />
            <Tooltip 
              contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
              cursor={{ stroke: '#cbd5e1', strokeWidth: 1, strokeDasharray: '4 4' }}
              formatter={(value: any) => [`${value}%`, 'Sell-through']}
            />
            <Line 
              type="monotone" 
              dataKey="value" 
              stroke="#10b981" 
              strokeWidth={3} 
              dot={false}
              activeDot={{ r: 6, fill: '#10b981', stroke: '#fff', strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
        </div>
      )}
    </div>
  );
};

// Region-specific category performance data
const regionalCategoryPerformance: Record<string, Array<{category: string, revenue: number, inventory: number}>> = {
  'North': [
    { category: 'Ethnic Wear', revenue: 145, inventory: 120 },
    { category: 'Kurtas', revenue: 132, inventory: 110 },
    { category: 'Bottoms', revenue: 98, inventory: 95 },
    { category: 'Winter Wear', revenue: 87, inventory: 105 },
    { category: 'Accessories', revenue: 65, inventory: 45 },
    { category: 'Footwear', revenue: 78, inventory: 85 },
  ],
  'South': [
    { category: 'Ethnic Wear', revenue: 128, inventory: 115 },
    { category: 'Kurtas', revenue: 118, inventory: 105 },
    { category: 'Bottoms', revenue: 89, inventory: 92 },
    { category: 'Winter Wear', revenue: 45, inventory: 75 },
    { category: 'Accessories', revenue: 72, inventory: 52 },
    { category: 'Footwear', revenue: 85, inventory: 88 },
  ],
  'Central': [
    { category: 'Ethnic Wear', revenue: 95, inventory: 125 },
    { category: 'Kurtas', revenue: 88, inventory: 108 },
    { category: 'Bottoms', revenue: 72, inventory: 98 },
    { category: 'Winter Wear', revenue: 68, inventory: 112 },
    { category: 'Accessories', revenue: 52, inventory: 58 },
    { category: 'Footwear', revenue: 65, inventory: 92 },
  ],
  'Northeast': [
    { category: 'Ethnic Wear', revenue: 112, inventory: 105 },
    { category: 'Kurtas', revenue: 102, inventory: 98 },
    { category: 'Bottoms', revenue: 78, inventory: 88 },
    { category: 'Winter Wear', revenue: 92, inventory: 95 },
    { category: 'Accessories', revenue: 58, inventory: 48 },
    { category: 'Footwear', revenue: 68, inventory: 78 },
  ],
  'West': [
    { category: 'Ethnic Wear', revenue: 165, inventory: 125 },
    { category: 'Kurtas', revenue: 148, inventory: 115 },
    { category: 'Bottoms', revenue: 115, inventory: 98 },
    { category: 'Winter Wear', revenue: 78, inventory: 92 },
    { category: 'Accessories', revenue: 82, inventory: 52 },
    { category: 'Footwear', revenue: 95, inventory: 88 },
  ],
  'East': [
    { category: 'Ethnic Wear', revenue: 75, inventory: 118 },
    { category: 'Kurtas', revenue: 68, inventory: 105 },
    { category: 'Bottoms', revenue: 55, inventory: 95 },
    { category: 'Winter Wear', revenue: 42, inventory: 102 },
    { category: 'Accessories', revenue: 38, inventory: 55 },
    { category: 'Footwear', revenue: 48, inventory: 88 },
  ],
  'Tier-2 Cities': [
    { category: 'Ethnic Wear', revenue: 88, inventory: 108 },
    { category: 'Kurtas', revenue: 82, inventory: 98 },
    { category: 'Bottoms', revenue: 68, inventory: 92 },
    { category: 'Winter Wear', revenue: 58, inventory: 95 },
    { category: 'Accessories', revenue: 45, inventory: 52 },
    { category: 'Footwear', revenue: 58, inventory: 82 },
  ]
};

export const CategoryPerformanceChart: React.FC<ChartProps> = ({ selectedRegion }) => {
  const categoryData = useMemo(() => {
    if (selectedRegion === 'All Regions') {
      // Calculate average across all regions
      const categories = ['Ethnic Wear', 'Kurtas', 'Bottoms', 'Winter Wear', 'Accessories', 'Footwear'];
      const regionKeys = Object.keys(regionalCategoryPerformance);
      
      return categories.map((category, idx) => {
        const avgRevenue = Math.round(
          regionKeys.reduce((sum, region) => 
            sum + regionalCategoryPerformance[region][idx].revenue, 0
          ) / regionKeys.length
        );
        const avgInventory = Math.round(
          regionKeys.reduce((sum, region) => 
            sum + regionalCategoryPerformance[region][idx].inventory, 0
          ) / regionKeys.length
        );
        return {
          category,
          revenue: avgRevenue,
          inventory: avgInventory
        };
      });
    } else {
      return regionalCategoryPerformance[selectedRegion];
    }
  }, [selectedRegion]);

  return (
    <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
      <div className="mb-6">
        <h3 className="text-lg font-bold text-slate-900">Category Performance</h3>
        <p className="text-sm text-slate-500">Revenue contribution (₹Cr) vs Inventory Share (%)</p>
        <div className="mt-2 inline-flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-1.5">
          <span className="text-xs font-medium text-blue-900">Formula:</span>
          <code className="text-xs font-mono text-blue-700 bg-white px-2 py-0.5 rounded border border-blue-200">
            Revenue = SUM(Sale Amount) | Inventory % = (Category Inv / Total Inv) × 100
          </code>
        </div>
      </div>
      <div style={{ width: '100%', height: 300, minHeight: 300, minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minWidth={0}>
        <BarChart 
          data={categoryData}
          margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
          <XAxis 
            dataKey="category" 
            axisLine={false} 
            tickLine={false}
            tick={{ fill: '#64748b', fontSize: 12 }}
            angle={-15}
            textAnchor="end"
            height={80}
          />
          <YAxis 
            axisLine={false} 
            tickLine={false}
            tick={{ fill: '#64748b', fontSize: 12 }}
          />
          <Tooltip 
            contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
            cursor={{ fill: '#f1f5f9' }}
            formatter={(value, name) => {
              if (name === 'revenue') return [`₹${value}Cr`, 'Revenue'];
              if (name === 'inventory') return [`${value}%`, 'Inventory Share'];
              return [value, name];
            }}
          />
          <Legend 
            verticalAlign="top" 
            height={36}
            iconType="circle"
            wrapperStyle={{ paddingBottom: '20px' }}
          />
          <Bar 
            dataKey="revenue" 
            name="Revenue (₹Cr)" 
            fill="#10b981" 
            radius={[8, 8, 0, 0]}
            maxBarSize={60}
          />
          <Bar 
            dataKey="inventory" 
            name="Inventory Share (%)" 
            fill="#3b82f6" 
            radius={[8, 8, 0, 0]}
            maxBarSize={60}
          />
        </BarChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
};

export const DiscountChart: React.FC<ChartProps> = ({ selectedRegion }) => {
  const discountData = useMemo(() => {
    if (selectedRegion === 'All Regions') {
      // Calculate average across all regions
      const categories = ['Dresses', 'Tops', 'Pants', 'Outerwear', 'Accessories', 'Footwear'];
      const regionKeys = Object.keys(regionalDiscountData);
      
      return categories.map((category, idx) => {
        const avgFullPrice = Math.round(
          regionKeys.reduce((sum, region) => 
            sum + regionalDiscountData[region][idx].fullPrice, 0
          ) / regionKeys.length
        );
        return {
          category,
          fullPrice: avgFullPrice,
          discounted: 100 - avgFullPrice
        };
      });
    } else {
      return regionalDiscountData[selectedRegion];
    }
  }, [selectedRegion]);

  return (
    <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm h-[350px]">
      <div className="mb-6">
        <h3 className="text-lg font-bold text-slate-900">Discount Analysis by Category</h3>
        <p className="text-sm text-slate-500">Full price vs discounted sales ratio</p>
        <div className="mt-2 inline-flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
          <span className="text-xs font-medium text-amber-900">Formula:</span>
          <code className="text-xs font-mono text-amber-700 bg-white px-2 py-0.5 rounded border border-amber-200">
            Discount % = ((MRP - Sale Price) / MRP) × 100
          </code>
        </div>
      </div>
      <div style={{ width: '100%', height: 260, minHeight: 260, minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={260} minWidth={0}>
        <BarChart 
          data={discountData} 
          layout="vertical" 
          barSize={24}
          margin={{ left: 20 }}
        >
          <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#e2e8f0" />
          <XAxis type="number" hide domain={[0, 100]} />
          <YAxis 
            dataKey="category" 
            type="category" 
            axisLine={false} 
            tickLine={false}
            tick={{ fill: '#64748b', fontSize: 12 }}
            width={80}
          />
          <Tooltip 
             cursor={{fill: '#f1f5f9'}}
             contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
          />
          <Legend verticalAlign="bottom" height={36} iconType="circle" />
          <Bar dataKey="fullPrice" name="Full Price" stackId="a" fill="#10b981" radius={[4, 0, 0, 4]} />
          <Bar dataKey="discounted" name="Discounted" stackId="a" fill="#f59e0b" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
};