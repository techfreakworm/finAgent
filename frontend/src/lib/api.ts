const BASE = '/api';

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ---- Types ----

export interface PortfolioData {
  capital: number;
  starting_capital: number;
  hard_floor: number;
  floor_distance: number;
  cumulative_pnl: number;
  total_trades: number;
  total_realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
  margin_used: number;
  paper_mode: boolean;
  strategies: Record<string, StrategySummary>;
}

export interface StrategySummary {
  strategy: string;
  total_trades: number;
  winners: number;
  total_pnl: number;
  avg_pnl: number;
  worst_trade: number;
  best_trade: number;
  total_costs: number;
  win_rate: number;
}

export interface Signal {
  id: number;
  strategy: string;
  symbol: string;
  direction: string;
  entry_price: number;
  stop_loss: number;
  lot_size: number;
  margin_required: number;
  confidence: number;
  reasoning: string;
  metadata: string;
  status: string;
  created_at: string;
}

export interface Trade {
  id: number;
  position_id: string;
  strategy: string;
  symbol: string;
  direction: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl_net: number;
  cost: number;
  exit_reason: string;
}

export interface OpenPosition {
  id: string;
  symbol: string;
  strategy: string;
  direction: string;
  entry_date: string;
  entry_price: number;
  quantity: number;
  margin_required: number;
  stop_loss: number;
  status: string;
}

export interface RiskData {
  capital: number;
  hard_floor: number;
  floor_distance: number;
  floor_distance_pct: number;
  floor_breached: boolean;
  peak_capital: number;
  drawdown_pct: number;
  margin_used: number;
  margin_utilization_pct: number;
  max_loss_allowed: number;
}

export interface DailyPnL {
  date: string;
  capital: number;
  cumulative_pnl: number;
  unrealized_pnl: number;
  n_open_positions: number;
}

export interface TaskStatus {
  id: string;
  name: string;
  status: 'running' | 'completed' | 'failed';
  result: unknown;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface Settings {
  paper_mode: boolean;
  starting_capital: number;
  hard_floor: number;
  nifty_enabled: boolean;
  nifty_min_vix: number;
  nifty_strangle_offset: number;
  equity_mr_enabled: boolean;
  equity_rsi_entry: number;
  equity_rsi_exit: number;
  equity_stop_loss_pct: number;
  equity_max_hold_days: number;
  equity_max_position_pct: number;
  momentum_enabled: boolean;
  momentum_lookback: number;
  momentum_top_n: number;
  dhan_connected: boolean;
  telegram_configured: boolean;
  [key: string]: unknown;
}

export interface LogEntry {
  id: number;
  event_type: string;
  message: string;
  data: string | null;
  created_at: string;
}

// ---- API ----

export const api = {
  // Portfolio
  getPortfolio: () => fetchJSON<PortfolioData>('/portfolio'),
  getPortfolioHistory: (days = 90) => fetchJSON<DailyPnL[]>(`/portfolio/history?days=${days}`),

  // Signals
  getSignals: (limit = 50) => fetchJSON<Signal[]>(`/signals?limit=${limit}`),
  approveSignal: (id: number) => postJSON(`/signals/${id}/approve`),
  rejectSignal: (id: number) => postJSON(`/signals/${id}/reject`),

  // Trades
  getTrades: (limit = 100) => fetchJSON<Trade[]>(`/trades?limit=${limit}`),
  getTradesSummary: () => fetchJSON<Record<string, StrategySummary>>('/trades/summary'),

  // Positions
  getPositions: () => fetchJSON<OpenPosition[]>('/positions'),
  closePosition: (id: string) => postJSON<{ position_id: string; margin_released: number; capital: number }>(`/positions/${id}/close`),

  // Risk
  getRisk: () => fetchJSON<RiskData>('/risk'),

  // Actions
  triggerScan: () => postJSON<{ task_id: string }>('/actions/scan'),
  triggerWorkflow: () => postJSON<{ task_id: string }>('/actions/workflow'),
  triggerReport: () => postJSON<{ task_id: string }>('/actions/report'),
  getTaskStatus: (id: string) => fetchJSON<TaskStatus>(`/actions/status/${id}`),
  getTasks: () => fetchJSON<TaskStatus[]>('/actions/tasks'),
  closeAllPositions: () => postJSON('/actions/close-all'),

  // Settings
  getSettings: () => fetchJSON<Settings>('/settings'),
  updateSettings: (s: Partial<Settings>) => putJSON<{ message: string }>('/settings', s),
  testDhan: () => postJSON<{ status: string; message: string }>('/settings/test-dhan'),
  testTelegram: () => postJSON<{ status: string; message: string }>('/settings/test-telegram'),
  getLlmStatus: () => fetchJSON<{ available: boolean; model: string }>('/settings/llm-status'),

  // Logs
  getLogs: (limit = 100) => fetchJSON<LogEntry[]>(`/logs?limit=${limit}`),

  // Accounts
  getAccounts: () => fetchJSON<any[]>('/accounts'),
  getActiveAccount: () => fetchJSON<any>('/accounts/active'),
  setActiveAccount: (id: string) => putJSON<any>('/accounts/active', { account_id: id }),
  createAccount: (body: any) => postJSON<any>('/accounts', body),
  resetAccount: (id: string) => postJSON<{ message: string; capital: number }>(`/accounts/${id}/reset`),

  // Scheduler
  getSchedulerStatus: () => fetchJSON<{ running: boolean; current_time: string; weekday: string; market_hours: boolean }>('/scheduler/status'),
  startScheduler: () => postJSON<{ status: string }>('/scheduler/start'),
  stopScheduler: () => postJSON<{ status: string }>('/scheduler/stop'),

  // Health (note: /health is NOT under /api prefix)
  getHealth: async () => {
    const res = await fetch('/health');
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json() as Promise<{ status: string; llm: { model: string; available: boolean }; scheduler: boolean }>;
  },
};
