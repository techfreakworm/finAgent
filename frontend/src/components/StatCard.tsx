import type { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  icon?: ReactNode;
  variant?: 'default' | 'green' | 'red' | 'yellow';
}

const variants = {
  default: 'border-[#2a2d3e]',
  green: 'border-emerald-500/30',
  red: 'border-red-500/30',
  yellow: 'border-amber-500/30',
};

const valueColors = {
  default: 'text-white',
  green: 'text-emerald-400',
  red: 'text-red-400',
  yellow: 'text-amber-400',
};

export default function StatCard({ label, value, sub, icon, variant = 'default' }: StatCardProps) {
  return (
    <div className={`bg-[#1e2235] rounded-xl border ${variants[variant]} p-4`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500 uppercase tracking-wider">{label}</span>
        {icon && <span className="text-gray-500">{icon}</span>}
      </div>
      <div className={`text-2xl font-semibold ${valueColors[variant]}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );
}
