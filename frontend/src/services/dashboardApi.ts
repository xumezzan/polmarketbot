export interface AdminStatus {
  api_alive: boolean;
  generated_at: string;
  last_scheduler_cycle_started_at: string | null;
  last_scheduler_cycle_finished_at: string | null;
  last_scheduler_cycle_fetched_news_count: number | null;
  last_scheduler_cycle_inserted_news_count: number | null;
  last_scheduler_cycle_error_count: number | null;
  last_error: string | null;
  news_items_count: number;
  analyses_count: number;
  signals_count: number;
  paper_trades_count: number;
  open_positions_count: number;
  live_orders_count: number;
  live_open_positions_count: number;
  execution_mode: string;
  live_trading_enabled: boolean;
  kill_switch_enabled: boolean;
  live_kill_switch_enabled: boolean;
  live_circuit_breaker_enabled: boolean;
  fetched_news_24h: number;
  scheduler_cycles_24h: number;
  failed_cycles_24h: number;
  provider_cooldowns: Record<string, { remaining_seconds: number; reason: string }>;
  inserted_news_24h: number;
  analyses_count_24h: number;
  llm_tokens_24h: number;
  llm_cost_24h: number;
  signals_count_24h: number;
  opened_trades_24h: number;
  closed_trades_24h: number;
}

export interface PaperStats {
  total_trades: number;
  closed_trades: number;
  open_positions: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
  avg_win_pnl: number;
  avg_loss_pnl: number;
  expectancy: number;
  closed_trade_ids: number[];
}

export interface PaperStatsResponse {
  generated_at: string;
  stats: PaperStats;
}

export interface OpenPosition {
  position_id: number;
  signal_id: number;
  market_id: string;
  market_question: string | null;
  side: string;
  entry_price: number;
  size_usd: number;
  shares: number;
  opened_at: string;
  holding_minutes: number;
}

export interface OpenPositionsResponse {
  generated_at: string;
  count: number;
  items: OpenPosition[];
}

export interface SignalAuditItem {
  signal_id: number;
  created_at: string;
  news_item_id: number;
  news_title: string;
  news_source: string;
  market_query: string;
  llm_reason: string;
  direction: string;
  confidence: number;
  relevance: number;
  market_id: string;
  market_question: string | null;
  signal_status: string;
  edge: number;
  market_price: number;
  fair_probability: number;
  risk_allow: boolean | null;
  risk_blockers: string[];
  approved_size_usd: number | null;
}

export interface SignalAuditResponse {
  generated_at: string;
  count: number;
  items: SignalAuditItem[];
}

export interface AnomalyReport {
  generated_at: string;
  window_hours: number;
  observations_count: number;
  hypotheses_count: number;
  top_hypotheses: Array<{
    id: number;
    generated_at: string;
    hypothesis_type: string;
    status: string;
    score: number;
    title: string;
    rationale: string;
  }>;
  notes: string[];
}

export interface DashboardSnapshot {
  status: AdminStatus;
  paperStats: PaperStats;
  openPositions: OpenPosition[];
  signalAudit: SignalAuditItem[];
  anomalies: AnomalyReport | null;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchDashboardSnapshot(): Promise<DashboardSnapshot> {
  const [status, statsResponse, positionsResponse, signalAuditResponse, anomalies] =
    await Promise.all([
      getJson<AdminStatus>("/admin/status"),
      getJson<PaperStatsResponse>("/admin/paper/stats"),
      getJson<OpenPositionsResponse>("/admin/positions/open"),
      getJson<SignalAuditResponse>("/admin/signals/audit?limit=20"),
      getJson<AnomalyReport>("/admin/anomaly-hunter/report?window_hours=24").catch(() => null),
    ]);

  return {
    status,
    paperStats: statsResponse.stats,
    openPositions: positionsResponse.items,
    signalAudit: signalAuditResponse.items,
    anomalies,
  };
}

export async function setPaperKillSwitch(enabled: boolean): Promise<void> {
  const path = enabled ? "/admin/kill-switch/on" : "/admin/kill-switch/off";
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
}
