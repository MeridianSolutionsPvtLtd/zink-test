import React from 'react';
import { ArrowUpRight, ArrowDownRight, MoreHorizontal } from 'lucide-react';
import { Metric } from '../../../../types';

interface StatCardProps {
  metric: Metric;
}

const StatCard: React.FC<StatCardProps> = ({ metric }) => {
  const isPositive = metric.status === 'positive';
  const isWarning = metric.status === 'warning';

  // Custom logic for color mapping based on design
  let trendColor = 'text-emerald-500';
  let Icon = ArrowUpRight;

  if (metric.status === 'negative') {
    trendColor = 'text-rose-500';
    Icon = ArrowDownRight;
  } else if (metric.status === 'warning') {
    trendColor = 'text-emerald-500'; // Still growing but maybe warning status overall? Following screenshot logic where 847 At-Risk is yellow icon but green trend.
  }

  if (metric.trend !== undefined) {
    if (metric.trend < 0) {
      Icon = ArrowDownRight;
      trendColor = 'text-rose-500';
    } else {
      Icon = ArrowUpRight;
      trendColor = 'text-emerald-500';
    }
  }

  return (
    <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm relative group hover:shadow-md transition-shadow">
      <div className="flex justify-between items-start mb-4">
        <h3 className="text-slate-500 font-medium text-sm">{metric.label}</h3>
        {metric.icon && (
          <div className={`p-2 rounded-lg ${metric.status === 'positive' ? 'bg-emerald-50 text-emerald-600' :
              metric.status === 'warning' ? 'bg-amber-50 text-amber-600' :
                metric.status === 'negative' ? 'bg-rose-50 text-rose-600' :
                  'bg-slate-100 text-slate-600'
            }`}>
            {metric.icon}
          </div>
        )}
      </div>

      <div className="flex items-baseline gap-1 mb-2">
        <span className="text-3xl font-bold text-slate-900">{metric.value}</span>
      </div>

      {metric.trend !== undefined && metric.trendLabel && (
        <div className="flex items-center gap-2 text-sm">
          <span className={`flex items-center font-semibold ${trendColor}`}>
            <Icon size={16} className="mr-1" />
            {Math.abs(metric.trend)}%
          </span>
          <span className="text-slate-400">{metric.trendLabel}</span>
        </div>
      )}
    </div>
  );
};

export default StatCard;