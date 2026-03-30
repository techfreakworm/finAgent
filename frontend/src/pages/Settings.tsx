import { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Settings as SettingsType } from '../lib/api';
import { Save, CheckCircle, XCircle, Zap, Bot, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';

function NumberInput({ label, value, onChange, tooltip, suffix }: {
  label: string; value: number; onChange: (v: number) => void; tooltip?: string; suffix?: string;
}) {
  return (
    <div>
      <label className="block text-sm text-gray-400 mb-1" title={tooltip}>{label}</label>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="px-3 py-2 bg-[#0f1117] border border-[#2a2d3e] rounded-lg text-white text-sm w-32 focus:outline-none focus:border-emerald-500"
        />
        {suffix && <span className="text-xs text-gray-500">{suffix}</span>}
      </div>
    </div>
  );
}

function Toggle({ label, checked, onChange, description }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; description?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <span className="text-sm text-white">{label}</span>
        {description && <p className="text-xs text-gray-500 mt-0.5">{description}</p>}
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={`w-10 h-5 rounded-full transition-colors relative ${checked ? 'bg-emerald-500' : 'bg-gray-600'}`}
      >
        <div className={`w-4 h-4 bg-white rounded-full absolute top-0.5 transition-transform ${checked ? 'translate-x-5' : 'translate-x-0.5'}`} />
      </button>
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const { data: llm } = useQuery({ queryKey: ['llm-status'], queryFn: api.getLlmStatus, refetchInterval: 15000 });

  const [form, setForm] = useState<Partial<SettingsType>>({});
  const [saving, setSaving] = useState(false);
  const [dhanTesting, setDhanTesting] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState('');

  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);

  const update = (key: string, value: unknown) => setForm(prev => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateSettings(form);
      toast.success('Settings saved');
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    } catch (e) {
      toast.error(`Save failed: ${e}`);
    }
    setSaving(false);
  };

  const handleModeToggle = (toLive: boolean) => {
    if (toLive) {
      if (liveConfirm !== 'CONFIRM') {
        toast.error('Type CONFIRM to enable live trading');
        return;
      }
      update('paper_mode', false);
      setLiveConfirm('');
    } else {
      update('paper_mode', true);
    }
  };

  const testDhan = async () => {
    setDhanTesting(true);
    try {
      const r = await api.testDhan();
      r.status === 'connected' ? toast.success(r.message) : toast.error(r.message);
    } catch (e) {
      toast.error(`Test failed: ${e}`);
    }
    setDhanTesting(false);
  };

  if (isLoading) return <div className="text-gray-500">Loading settings...</div>;

  return (
    <div className="max-w-2xl">
      <h2 className="text-xl font-semibold text-white mb-2">Settings</h2>
      <p className="text-sm text-gray-500 mb-6">Configure trading parameters, strategies, and connections</p>

      {/* Trading Mode */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">Trading Mode</h3>
        <div className="flex items-center gap-4">
          <button
            onClick={() => handleModeToggle(false)}
            className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
              form.paper_mode ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' : 'bg-[#0f1117] text-gray-500 border-[#2a2d3e]'
            }`}
          >📝 Paper</button>
          <button
            onClick={() => handleModeToggle(true)}
            className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
              !form.paper_mode ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-[#0f1117] text-gray-500 border-[#2a2d3e]'
            }`}
          >🔴 Live</button>
        </div>
        {form.paper_mode && (
          <div className="mt-3 flex items-center gap-2">
            <input
              type="text"
              value={liveConfirm}
              onChange={(e) => setLiveConfirm(e.target.value)}
              placeholder='Type "CONFIRM" to enable live mode'
              className="px-3 py-1.5 bg-[#0f1117] border border-[#2a2d3e] rounded text-sm text-white placeholder-gray-600 w-64 focus:outline-none"
            />
          </div>
        )}
      </section>

      {/* Capital & Risk */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">Capital & Risk</h3>
        <div className="grid grid-cols-2 gap-4">
          <NumberInput label="Starting Capital" value={form.starting_capital || 500000} onChange={v => update('starting_capital', v)} suffix="₹" />
          <NumberInput label="Hard Floor" value={form.hard_floor || 400000} onChange={v => update('hard_floor', v)} suffix="₹" tooltip="All positions will be force-exited if capital drops below this" />
        </div>
      </section>

      {/* NIFTY Strangle */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <Toggle label="NIFTY Short Strangle" checked={form.nifty_enabled ?? true} onChange={v => update('nifty_enabled', v)} description="Sell OTM NIFTY weekly options every Monday" />
        {form.nifty_enabled && (
          <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-[#2a2d3e]">
            <NumberInput label="Min VIX for Entry" value={form.nifty_min_vix || 12} onChange={v => update('nifty_min_vix', v)} tooltip="Only sell strangles when VIX is above this" />
            <NumberInput label="Strangle Offset" value={form.nifty_strangle_offset || 2} onChange={v => update('nifty_strangle_offset', v)} tooltip="Strikes away from ATM" />
          </div>
        )}
      </section>

      {/* Equity Mean Reversion */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <Toggle label="Equity Mean Reversion" checked={form.equity_mr_enabled ?? true} onChange={v => update('equity_mr_enabled', v)} description="Buy oversold stocks (RSI < 30), sell when RSI recovers" />
        {form.equity_mr_enabled && (
          <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-[#2a2d3e]">
            <NumberInput label="RSI Entry (buy below)" value={form.equity_rsi_entry || 30} onChange={v => update('equity_rsi_entry', v)} />
            <NumberInput label="RSI Exit (sell above)" value={form.equity_rsi_exit || 50} onChange={v => update('equity_rsi_exit', v)} />
            <NumberInput label="Stop Loss" value={form.equity_stop_loss_pct || 5} onChange={v => update('equity_stop_loss_pct', v)} suffix="%" />
            <NumberInput label="Max Hold" value={form.equity_max_hold_days || 20} onChange={v => update('equity_max_hold_days', v)} suffix="days" />
          </div>
        )}
      </section>

      {/* Momentum */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <Toggle label="Equity Momentum" checked={form.momentum_enabled ?? true} onChange={v => update('momentum_enabled', v)} description="Monthly rotation into top momentum stocks" />
        {form.momentum_enabled && (
          <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-[#2a2d3e]">
            <NumberInput label="Lookback" value={form.momentum_lookback || 12} onChange={v => update('momentum_lookback', v)} suffix="months" />
            <NumberInput label="Top N Stocks" value={form.momentum_top_n || 5} onChange={v => update('momentum_top_n', v)} />
          </div>
        )}
      </section>

      {/* Connections */}
      <section className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-4">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">Connections</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2">
            <div className="flex items-center gap-2">
              <Zap size={14} className={settings?.dhan_connected ? 'text-emerald-400' : 'text-red-400'} />
              <span className="text-sm text-white">Dhan API</span>
              {settings?.dhan_connected && <span className="text-xs text-emerald-400">Connected</span>}
            </div>
            <button onClick={testDhan} disabled={dhanTesting} className="px-3 py-1 bg-[#0f1117] border border-[#2a2d3e] rounded text-xs text-gray-400 hover:text-white transition-colors">
              {dhanTesting ? <Loader2 size={12} className="animate-spin" /> : 'Test Connection'}
            </button>
          </div>
          <div className="flex items-center justify-between py-2">
            <div className="flex items-center gap-2">
              <Bot size={14} className={llm?.available ? 'text-emerald-400' : 'text-amber-400'} />
              <span className="text-sm text-white">Local LLM</span>
              <span className={`text-xs ${llm?.available ? 'text-emerald-400' : 'text-amber-400'}`}>
                {llm?.available ? llm.model : 'Not loaded'}
              </span>
            </div>
          </div>
          <div className="flex items-center justify-between py-2">
            <div className="flex items-center gap-2">
              {settings?.telegram_configured
                ? <CheckCircle size={14} className="text-emerald-400" />
                : <XCircle size={14} className="text-gray-500" />}
              <span className="text-sm text-white">Telegram</span>
              <span className="text-xs text-gray-500">{settings?.telegram_configured ? 'Configured' : 'Not configured'}</span>
            </div>
          </div>
        </div>
      </section>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="flex items-center gap-2 px-6 py-2.5 bg-emerald-500 text-white rounded-lg text-sm font-medium hover:bg-emerald-600 disabled:opacity-50 transition-colors"
      >
        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
        Save Settings
      </button>
    </div>
  );
}
