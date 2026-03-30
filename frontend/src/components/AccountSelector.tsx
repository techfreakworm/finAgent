import { useState } from 'react';
import { useAccount } from '../lib/account-context';
import { ChevronDown, FileText, Zap, PlayCircle } from 'lucide-react';

const typeConfig = {
  paper: { icon: FileText, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30', label: '📝' },
  live: { icon: Zap, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: '🔴' },
  replay: { icon: PlayCircle, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', label: '📊' },
};

export default function AccountSelector() {
  const { activeAccount, accounts, switchAccount } = useAccount();
  const [open, setOpen] = useState(false);

  if (!activeAccount) return null;

  const cfg = typeConfig[activeAccount.type as keyof typeof typeConfig] || typeConfig.paper;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${cfg.bg} ${cfg.color} ${cfg.border}`}
      >
        <span>{cfg.label}</span>
        <span>{activeAccount.label}</span>
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 bottom-full mb-1 w-56 bg-[#1e2235] border border-[#2a2d3e] rounded-lg shadow-xl z-50 py-1 max-h-72 overflow-y-auto">
            {accounts.map((acc) => {
              const c = typeConfig[acc.type as keyof typeof typeConfig] || typeConfig.paper;
              const isActive = acc.id === activeAccount.id;
              return (
                <button
                  key={acc.id}
                  onClick={() => { switchAccount(acc.id); setOpen(false); }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors ${
                    isActive ? 'bg-[#252840] text-white' : 'text-gray-400 hover:bg-[#252840] hover:text-white'
                  }`}
                >
                  <span>{c.label}</span>
                  <div className="flex-1">
                    <div className={isActive ? 'text-white' : ''}>{acc.label}</div>
                    <div className="text-xs text-gray-600">
                      ₹{(acc.current_capital / 100000).toFixed(1)}L
                    </div>
                  </div>
                  {isActive && <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
