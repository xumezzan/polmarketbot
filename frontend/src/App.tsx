import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CircleDollarSign,
  Database,
  Newspaper,
  RefreshCw,
  ShieldAlert,
  Square,
  TrendingUp,
  Zap,
} from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import {
  type DashboardSnapshot,
  type OpenPosition,
  type SignalAuditItem,
  fetchDashboardSnapshot,
  setPaperKillSwitch,
} from "./services/dashboardApi";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const number = new Intl.NumberFormat("en-US");

function formatMoney(value: number | null | undefined) {
  return money.format(value ?? 0);
}

function formatPercent(value: number | null | undefined) {
  return `${(((value ?? 0) <= 1 ? value ?? 0 : (value ?? 0) / 100) * 100).toFixed(1)}%`;
}

function formatTime(value: string | null | undefined) {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function compactId(value: string | number) {
  const raw = String(value);
  return raw.length > 12 ? `${raw.slice(0, 5)}...${raw.slice(-4)}` : raw;
}

function DashboardMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "bad" | "info";
}) {
  return (
    <div className="border border-brand-border-dim bg-brand-surface p-3">
      <div className="mb-1 text-[10px] font-bold uppercase text-white/40">{label}</div>
      <div
        className={cn(
          "truncate font-mono text-lg font-black",
          tone === "good" && "text-emerald-400",
          tone === "bad" && "text-rose-400",
          tone === "info" && "text-blue-400",
          tone === "neutral" && "text-white",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function StatusBadge({ active, children }: { active: boolean; children: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-1 font-mono text-[10px] font-bold uppercase",
        active
          ? "border-emerald-500/30 bg-emerald-950/40 text-emerald-400"
          : "border-rose-500/30 bg-rose-950/40 text-rose-400",
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", active ? "bg-emerald-400" : "bg-rose-400")} />
      {children}
    </span>
  );
}

function SignalCard({ signal }: { signal: SignalAuditItem }) {
  const allow = signal.risk_allow === true;
  const rejected = signal.risk_allow === false || signal.signal_status === "REJECTED";

  return (
    <article className="border border-brand-border bg-brand-surface p-3">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xs font-bold uppercase text-white">
            {signal.market_question || signal.market_query || signal.market_id}
          </div>
          <div className="mt-1 truncate font-mono text-[10px] text-white/45">
            news #{signal.news_item_id} / signal #{signal.signal_id}
          </div>
        </div>
        <span
          className={cn(
            "shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] font-bold uppercase",
            allow && "border-emerald-500/30 bg-emerald-950/40 text-emerald-400",
            rejected && "border-rose-500/30 bg-rose-950/40 text-rose-400",
            !allow && !rejected && "border-blue-500/30 bg-blue-950/40 text-blue-400",
          )}
        >
          {allow ? "approved" : rejected ? "blocked" : signal.signal_status}
        </span>
      </div>

      <p className="line-clamp-2 text-[11px] leading-5 text-white/65">{signal.news_title}</p>

      <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-[10px]">
        <div>
          <div className="text-white/35">edge</div>
          <div className={signal.edge >= 0 ? "text-emerald-400" : "text-rose-400"}>
            {formatPercent(signal.edge)}
          </div>
        </div>
        <div>
          <div className="text-white/35">fair</div>
          <div className="text-white/70">{formatPercent(signal.fair_probability)}</div>
        </div>
        <div>
          <div className="text-white/35">market</div>
          <div className="text-white/70">{formatPercent(signal.market_price)}</div>
        </div>
      </div>

      {signal.risk_blockers.length > 0 && (
        <div className="mt-3 truncate font-mono text-[10px] text-amber-300/80">
          {signal.risk_blockers.join(", ")}
        </div>
      )}
    </article>
  );
}

function PositionRow({ position }: { position: OpenPosition }) {
  return (
    <tr className="border-b border-brand-border-dim hover:bg-white/[0.02]">
      <td className="p-3 font-bold text-white">{compactId(position.signal_id)}</td>
      <td className="max-w-[280px] truncate p-3 text-white/70">
        {position.market_question || position.market_id}
      </td>
      <td className={cn("p-3 font-bold", position.side === "YES" ? "text-emerald-400" : "text-rose-400")}>
        {position.side}
      </td>
      <td className="p-3 text-white/70">{formatMoney(position.size_usd)}</td>
      <td className="p-3 text-white/50">{formatPercent(position.entry_price)}</td>
      <td className="p-3 text-right text-white/50">{Math.round(position.holding_minutes)}m</td>
    </tr>
  );
}

function buildEventStream(snapshot: DashboardSnapshot) {
  const events = snapshot.signalAudit.slice(0, 10).map((signal) => ({
    tone: signal.risk_allow ? "good" : signal.risk_allow === false ? "bad" : "info",
    time: formatTime(signal.created_at),
    text: `${signal.direction} ${signal.signal_status}: ${signal.news_title}`,
  }));

  if (snapshot.status.last_error) {
    events.unshift({
      tone: "bad",
      time: formatTime(snapshot.status.generated_at),
      text: snapshot.status.last_error,
    });
  }

  return events;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isToggling, setIsToggling] = useState(false);

  async function refresh() {
    try {
      const data = await fetchDashboardSnapshot();
      setSnapshot(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoading(false);
    }
  }

  async function toggleKillSwitch() {
    if (!snapshot) return;
    setIsToggling(true);
    try {
      await setPaperKillSwitch(!snapshot.status.kill_switch_enabled);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsToggling(false);
    }
  }

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, []);

  const exposure = useMemo(
    () => snapshot?.openPositions.reduce((sum, position) => sum + position.size_usd, 0) ?? 0,
    [snapshot],
  );
  const events = snapshot ? buildEventStream(snapshot) : [];
  const openPositions = snapshot?.openPositions ?? [];
  const signals = snapshot?.signalAudit ?? [];

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-brand-bg">
        <div className="flex flex-col items-center gap-4">
          <div className="h-12 w-12 animate-spin rounded-full border-4 border-brand-accent border-t-transparent" />
          <p className="font-mono text-[10px] font-bold uppercase text-white/40">Loading dashboard</p>
        </div>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-brand-bg p-6 text-white">
        <div className="max-w-lg border border-brand-border bg-brand-surface p-5">
          <div className="mb-2 flex items-center gap-2 text-rose-400">
            <AlertTriangle className="h-4 w-4" />
            <span className="font-bold uppercase">Dashboard API unavailable</span>
          </div>
          <p className="font-mono text-xs text-white/60">{error}</p>
        </div>
      </div>
    );
  }

  const isOperational = snapshot.status.api_alive && !snapshot.status.kill_switch_enabled;
  const pnlTone = snapshot.paperStats.total_pnl >= 0 ? "good" : "bad";

  return (
    <div className="min-h-screen bg-brand-bg text-[#D1D5DB] lg:h-screen lg:overflow-hidden">
      <div className="flex min-h-screen flex-col border-4 border-brand-border-dim lg:h-screen">
        <header className="flex shrink-0 flex-col gap-4 border-b border-brand-border bg-brand-surface px-4 py-4 md:flex-row md:items-center md:justify-between md:px-6">
          <div className="flex items-center gap-4">
            <div className="flex h-9 w-9 items-center justify-center bg-brand-accent shadow-[0_0_14px_rgba(16,185,129,0.25)]">
              <Zap className="h-5 w-5 fill-current text-brand-bg" />
            </div>
            <div>
              <h1 className="text-sm font-black uppercase text-white">Polymarket News Trading</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <StatusBadge active={isOperational}>
                  {isOperational ? "paper engine active" : "paper engine blocked"}
                </StatusBadge>
                <span className="font-mono text-[10px] uppercase text-white/35">
                  mode: {snapshot.status.execution_mode}
                </span>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]">
            <div className="text-right">
              <div className="text-[9px] uppercase text-white/35">Last refresh</div>
              <div className="text-white/70">{formatTime(snapshot.status.generated_at)}</div>
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              className="inline-flex h-9 items-center gap-2 border border-brand-border px-3 text-white/70 transition hover:border-blue-400 hover:text-blue-300"
              title="Refresh"
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => void toggleKillSwitch()}
              disabled={isToggling}
              className={cn(
                "inline-flex h-9 items-center gap-2 border px-3 font-bold uppercase transition disabled:opacity-50",
                snapshot.status.kill_switch_enabled
                  ? "border-emerald-500 bg-emerald-950/30 text-emerald-400 hover:bg-emerald-500 hover:text-brand-bg"
                  : "border-rose-500 bg-rose-950/30 text-rose-400 hover:bg-rose-500 hover:text-white",
              )}
            >
              {snapshot.status.kill_switch_enabled ? <Zap className="h-4 w-4" /> : <Square className="h-4 w-4" />}
              {snapshot.status.kill_switch_enabled ? "Disable kill switch" : "Enable kill switch"}
            </button>
          </div>
        </header>

        {error && (
          <div className="border-b border-amber-500/30 bg-amber-950/30 px-4 py-2 font-mono text-xs text-amber-200">
            {error}
          </div>
        )}

        <main className="grid flex-1 grid-cols-1 gap-px overflow-auto bg-brand-border lg:grid-cols-12 lg:overflow-hidden">
          <section className="flex min-h-[360px] flex-col overflow-hidden bg-brand-panel lg:col-span-3">
            <div className="flex shrink-0 items-center justify-between border-b border-brand-border bg-brand-surface p-3">
              <span className="flex items-center gap-2 text-[11px] font-bold uppercase text-white">
                <Newspaper className="h-3.5 w-3.5 text-blue-400" />
                News and Signals
              </span>
              <span className="rounded border border-blue-500/20 bg-blue-950/40 px-2 py-1 font-mono text-[10px] text-blue-300">
                {number.format(snapshot.status.inserted_news_24h)} / 24h
              </span>
            </div>
            <div className="custom-scrollbar flex-1 overflow-y-auto">
              {events.length === 0 ? (
                <div className="p-10 text-center text-xs italic text-white/25">No signal events yet</div>
              ) : (
                events.map((event, index) => (
                  <div key={`${event.time}-${index}`} className="border-b border-brand-border-dim p-3">
                    <div className="mb-1 flex justify-between gap-3 font-mono text-[10px]">
                      <span className="text-blue-300">{event.time}</span>
                      <span
                        className={cn(
                          event.tone === "good" && "text-emerald-400",
                          event.tone === "bad" && "text-rose-400",
                          event.tone === "info" && "text-white/35",
                        )}
                      >
                        {event.tone.toUpperCase()}
                      </span>
                    </div>
                    <p className="line-clamp-3 text-[11px] leading-5 text-white/70">{event.text}</p>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="flex min-h-[640px] flex-col overflow-hidden bg-brand-panel lg:col-span-6">
            <div className="grid flex-1 grid-rows-[minmax(300px,1fr)_minmax(300px,1fr)] overflow-hidden">
              <div className="flex flex-col overflow-hidden border-b border-brand-border">
                <div className="flex shrink-0 items-center justify-between border-b border-brand-border bg-brand-surface p-3">
                  <span className="flex items-center gap-2 text-[11px] font-bold uppercase text-white">
                    <TrendingUp className="h-3.5 w-3.5 text-amber-400" />
                    Signal Engine
                  </span>
                  <span className="font-mono text-[10px] uppercase text-amber-300">
                    {snapshot.status.signals_count_24h} signals / 24h
                  </span>
                </div>
                <div className="custom-scrollbar grid flex-1 gap-3 overflow-y-auto p-4 md:grid-cols-2">
                  {signals.length === 0 ? (
                    <div className="col-span-full flex items-center justify-center text-[10px] font-black uppercase text-white/20">
                      Waiting for candidate signals
                    </div>
                  ) : (
                    signals.map((signal) => <SignalCard key={signal.signal_id} signal={signal} />)
                  )}
                </div>
              </div>

              <div className="flex flex-col overflow-hidden bg-brand-surface/20">
                <div className="flex shrink-0 items-center justify-between border-b border-brand-border bg-brand-surface p-3">
                  <span className="flex items-center gap-2 text-[11px] font-bold uppercase text-white">
                    <CircleDollarSign className="h-3.5 w-3.5 text-emerald-400" />
                    Open Positions
                  </span>
                  <span className="font-mono text-[10px] uppercase text-white/35">
                    exposure {formatMoney(exposure)}
                  </span>
                </div>
                <div className="custom-scrollbar flex-1 overflow-auto">
                  <table className="w-full min-w-[720px] text-left font-mono text-[11px]">
                    <thead className="sticky top-0 bg-brand-surface/95">
                      <tr className="border-b border-brand-border-dim text-[9px] uppercase text-white/35">
                        <th className="p-3">Signal</th>
                        <th className="p-3">Market</th>
                        <th className="p-3">Side</th>
                        <th className="p-3">Size</th>
                        <th className="p-3">Entry</th>
                        <th className="p-3 text-right">Hold</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openPositions.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="p-12 text-center text-[10px] font-black uppercase text-white/20">
                            No active paper positions
                          </td>
                        </tr>
                      ) : (
                        openPositions.map((position) => (
                          <PositionRow key={position.position_id} position={position} />
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </section>

          <section className="flex min-h-[520px] flex-col overflow-hidden bg-brand-panel lg:col-span-3">
            <div className="shrink-0 border-b border-brand-border bg-brand-surface p-3">
              <span className="flex items-center gap-2 text-[11px] font-bold uppercase text-white">
                <ShieldAlert className="h-3.5 w-3.5 text-rose-400" />
                Risk and Analytics
              </span>
            </div>
            <div className="custom-scrollbar flex-1 space-y-4 overflow-y-auto p-4">
              <DashboardMetric label="Total PnL" value={formatMoney(snapshot.paperStats.total_pnl)} tone={pnlTone} />
              <div className="grid grid-cols-2 gap-2">
                <DashboardMetric label="Win Rate" value={formatPercent(snapshot.paperStats.win_rate)} tone="good" />
                <DashboardMetric label="Expectancy" value={formatMoney(snapshot.paperStats.expectancy)} tone={pnlTone} />
                <DashboardMetric label="Open" value={String(snapshot.paperStats.open_positions)} tone="info" />
                <DashboardMetric label="Closed" value={String(snapshot.paperStats.closed_trades)} />
              </div>

              <div className="border border-brand-border-dim bg-brand-surface p-3">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-bold uppercase text-white/70">
                  <Activity className="h-3.5 w-3.5 text-blue-400" />
                  Scheduler
                </div>
                <div className="space-y-2 font-mono text-[10px]">
                  <div className="flex justify-between gap-4">
                    <span className="text-white/35">cycles 24h</span>
                    <span className="text-white/75">{snapshot.status.scheduler_cycles_24h}</span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-white/35">failed 24h</span>
                    <span className={snapshot.status.failed_cycles_24h ? "text-rose-400" : "text-emerald-400"}>
                      {snapshot.status.failed_cycles_24h}
                    </span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-white/35">last finish</span>
                    <span className="text-white/75">{formatTime(snapshot.status.last_scheduler_cycle_finished_at)}</span>
                  </div>
                </div>
              </div>

              <div className="border border-brand-border-dim bg-black/30 p-3">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-bold uppercase text-white/70">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-300" />
                  Anomaly Hunter
                </div>
                <div className="space-y-2 font-mono text-[10px]">
                  {snapshot.anomalies?.top_hypotheses?.length ? (
                    snapshot.anomalies.top_hypotheses.slice(0, 5).map((item) => (
                      <div key={item.id} className="border-b border-white/5 pb-2 last:border-b-0 last:pb-0">
                        <div className="flex justify-between gap-3">
                          <span className="truncate text-white/70">{item.title}</span>
                          <span className="text-amber-300">{Math.round(item.score)}</span>
                        </div>
                        <div className="mt-1 truncate text-white/35">{item.status}</div>
                      </div>
                    ))
                  ) : (
                    <div className="text-white/30">No open hypotheses</div>
                  )}
                </div>
              </div>

              <div className="border border-brand-border-dim bg-brand-surface p-3">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-bold uppercase text-white/70">
                  <Database className="h-3.5 w-3.5 text-emerald-400" />
                  Database Counters
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono text-[10px]">
                  <div>
                    <div className="text-white/35">news</div>
                    <div className="text-white/75">{number.format(snapshot.status.news_items_count)}</div>
                  </div>
                  <div>
                    <div className="text-white/35">analyses</div>
                    <div className="text-white/75">{number.format(snapshot.status.analyses_count)}</div>
                  </div>
                  <div>
                    <div className="text-white/35">trades</div>
                    <div className="text-white/75">{number.format(snapshot.status.paper_trades_count)}</div>
                  </div>
                  <div>
                    <div className="text-white/35">llm cost 24h</div>
                    <div className="text-white/75">{formatMoney(snapshot.status.llm_cost_24h)}</div>
                  </div>
                </div>
              </div>
            </div>

            <footer className="shrink-0 border-t border-brand-border bg-brand-bg p-4 font-mono text-[10px] text-white/35">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span>live trading</span>
                <span className={snapshot.status.live_trading_enabled ? "text-emerald-400" : "text-white/35"}>
                  {snapshot.status.live_trading_enabled ? "enabled" : "disabled"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>live guard</span>
                <span
                  className={
                    snapshot.status.live_kill_switch_enabled || snapshot.status.live_circuit_breaker_enabled
                      ? "text-rose-400"
                      : "text-emerald-400"
                  }
                >
                  {snapshot.status.live_kill_switch_enabled || snapshot.status.live_circuit_breaker_enabled
                    ? "blocked"
                    : "clear"}
                </span>
              </div>
            </footer>
          </section>
        </main>
      </div>
    </div>
  );
}
