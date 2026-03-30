import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import AccountSelector from './AccountSelector';
import { LayoutDashboard, Signal, TrendingUp, Shield, Gamepad2, Settings, ScrollText, BarChart3, PlayCircle, Menu, X } from 'lucide-react';

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/performance', label: 'Performance', icon: BarChart3 },
  { to: '/signals', label: 'Signals', icon: Signal },
  { to: '/trades', label: 'Trades', icon: TrendingUp },
  { to: '/risk', label: 'Risk', icon: Shield },
  { to: '/control', label: 'Control Panel', icon: Gamepad2 },
  { to: '/replay', label: 'Replay', icon: PlayCircle },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/logs', label: 'Logs', icon: ScrollText },
];

export default function Layout() {
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: api.getHealth, refetchInterval: 30000 });
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Close sidebar on navigation (mobile)
  const handleNavClick = () => setSidebarOpen(false);

  return (
    <div className="min-h-screen flex relative">
      {/* Mobile header */}
      <header className="fixed top-0 left-0 right-0 z-40 bg-[#1a1d2e] border-b border-[#2a2d3e] flex items-center justify-between px-4 py-3 lg:hidden">
        <button onClick={() => setSidebarOpen(!sidebarOpen)} className="text-gray-400 hover:text-white">
          {sidebarOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
        <h1 className="text-base font-bold text-white">
          <span className="text-emerald-400">Fin</span>Agent
        </h1>
        <div className="w-6" /> {/* Spacer for centering */}
      </header>

      {/* Overlay for mobile sidebar */}
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed top-0 left-0 bottom-0 z-50 w-56 bg-[#1a1d2e] border-r border-[#2a2d3e] flex flex-col
        transition-transform duration-200
        lg:static lg:translate-x-0
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        <div className="p-4 border-b border-[#2a2d3e]">
          <h1 className="text-lg font-bold text-white tracking-tight">
            <span className="text-emerald-400">Fin</span>Agent
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">AI Trading System</p>
        </div>

        <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={handleNavClick}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-emerald-500/10 text-emerald-400'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-[#252840]'
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Status footer */}
        <div className="p-3 border-t border-[#2a2d3e] space-y-1.5">
          <AccountSelector />
          <div className="flex items-center gap-2 px-2 py-1 text-xs text-gray-500">
            <div className={`w-1.5 h-1.5 rounded-full ${health?.llm?.available ? 'bg-emerald-400' : 'bg-gray-600'}`} />
            LLM: {health?.llm?.available ? 'Ready' : 'Offline'}
          </div>
          <div className="flex items-center gap-2 px-2 py-1 text-xs text-gray-500">
            <div className={`w-1.5 h-1.5 rounded-full ${health?.status === 'ok' ? 'bg-emerald-400' : 'bg-red-400'}`} />
            API: {health?.status === 'ok' ? 'Connected' : 'Error'}
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 pt-14 lg:pt-0 p-4 lg:p-6 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
