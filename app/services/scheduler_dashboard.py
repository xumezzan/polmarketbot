from __future__ import annotations

from collections import deque
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from queue import Empty, SimpleQueue
import select
import sys
import threading
from urllib.parse import urlparse

import sqlalchemy as sa

from app.config import Settings
from app.database import AsyncSessionLocal
from app.models.enums import PositionStatus, TradeStatus
from app.models.position import Position
from app.models.trade import PaperTrade
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.trade_repo import TradeRepository
from app.schemas.scheduler import SchedulerCycleResult

try:
    from rich import box
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - exercised only when rich is not installed
    box = None
    Console = None
    Group = None
    Layout = None
    Live = None
    Panel = None
    Syntax = None
    Table = None
    Text = None

with suppress(ImportError):  # pragma: no cover - platform-specific import
    import termios
    import tty


def ensure_rich_available() -> None:
    if Live is None:
        raise RuntimeError(
            "Dashboard mode requires the 'rich' package. Install dependencies from requirements.txt."
        )


@dataclass(slots=True)
class DashboardTradeRow:
    market: str
    pick: str
    entry_price: float
    size_usd: float
    pnl: float | None
    status: str


@dataclass(slots=True)
class DashboardPositionRow:
    market: str
    side: str
    entry_price: float
    size_usd: float
    holding_minutes: float


@dataclass(slots=True)
class DashboardCycleRow:
    cycle_id: str
    status: str
    processed_news: int
    actionable_signals: int
    approved_signals: int
    opened_positions: int
    errors: int


@dataclass(slots=True)
class DashboardSnapshot:
    total_pnl: float = 0.0
    total_trades: int = 0
    closed_trades: int = 0
    open_positions: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    open_exposure_usd: float = 0.0
    realized_pnl_24h: float = 0.0
    biggest_win: float = 0.0
    cycles_24h: int = 0
    failed_cycles_24h: int = 0
    latest_cycle_status: str = "n/a"
    latest_cycle_processed_news: int = 0
    latest_cycle_actionable_signals: int = 0
    latest_cycle_approved_signals: int = 0
    latest_cycle_opened_positions: int = 0
    latest_cycle_closed_positions: int = 0
    latest_cycle_errors: int = 0
    recent_trades: list[DashboardTradeRow] | None = None
    open_position_rows: list[DashboardPositionRow] | None = None
    top_win_rows: list[DashboardTradeRow] | None = None
    top_loss_rows: list[DashboardTradeRow] | None = None
    recent_cycle_rows: list[DashboardCycleRow] | None = None
    recent_cycle_ids: list[str] | None = None


@dataclass(slots=True)
class DashboardLogLine:
    timestamp: str
    tag: str
    message: str
    style: str


class SchedulerDashboard:
    """Rich-based live terminal for the scheduler loop."""

    def __init__(self, *, settings: Settings, interval_minutes: float | None = None) -> None:
        ensure_rich_available()
        self.settings = settings
        self.interval_minutes = interval_minutes or settings.scheduler_interval_minutes
        self.console = Console()
        self.brand_name = "PENNY SNIPER"
        self.account_name = "yourbot"
        self.source_file = "penny_sniper.py"
        self.snapshot = DashboardSnapshot(recent_trades=[], recent_cycle_ids=[])
        self.log_lines: deque[DashboardLogLine] = deque(maxlen=200)
        self.status = "IDLE"
        self.main_view = "CLOSED"
        self.category_filter = "ALL"
        self.cycle_number = 0
        self.active_cycle_id: str | None = None
        self.next_run_at: datetime | None = None
        self.latest_result: SchedulerCycleResult | None = None
        self.last_error: str | None = None
        self._live: Live | None = None
        self._key_queue: SimpleQueue[str] = SimpleQueue()
        self._keyboard_stop = threading.Event()
        self._keyboard_thread: threading.Thread | None = None
        self._manual_refresh_requested = False
        self.exit_requested = False

    @contextmanager
    def run(self):
        live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=4,
            screen=True,
            transient=False,
        )
        self._live = live
        with live, self._keyboard_listener():
            self.refresh()
            yield self
        self._live = None

    async def refresh_data(self) -> None:
        now = datetime.now(UTC)
        since_24h = now - timedelta(hours=24)
        async with AsyncSessionLocal() as session:
            trade_repository = TradeRepository(session)
            cycle_repository = SchedulerCycleRepository(session)

            stats = await trade_repository.get_trade_statistics()
            recent_trades = await trade_repository.list_recent_closed_trades(limit=10)
            open_positions = await trade_repository.list_open_positions()
            top_wins = await trade_repository.list_top_closed_trades(limit=20, descending=True)
            top_losses = await trade_repository.list_top_closed_trades(limit=20, descending=False)
            recent_cycles = await cycle_repository.list_recent(limit=5)
            cycles_24h = await cycle_repository.count_cycles_since(since=since_24h)
            failed_cycles_24h = await cycle_repository.count_failed_cycles_since(since=since_24h)
            realized_pnl_24h = await trade_repository.sum_realized_pnl_since(since=since_24h)

            open_exposure_stmt = sa.select(sa.func.coalesce(sa.func.sum(Position.size_usd), 0)).where(
                Position.status == PositionStatus.OPEN
            )
            biggest_win_stmt = sa.select(sa.func.coalesce(sa.func.max(PaperTrade.pnl), 0)).where(
                PaperTrade.status == TradeStatus.CLOSED,
                PaperTrade.pnl.is_not(None),
            )

            open_exposure = float((await session.execute(open_exposure_stmt)).scalar_one())
            biggest_win_raw = (await session.execute(biggest_win_stmt)).scalar_one()
            biggest_win = float(biggest_win_raw if biggest_win_raw is not None else 0.0)
            latest_cycle = recent_cycles[0] if recent_cycles else None

        self.snapshot = DashboardSnapshot(
            total_pnl=float(stats.get("total_pnl", 0.0)),
            total_trades=int(stats.get("total_trades", 0)),
            closed_trades=int(stats.get("closed_trades", 0)),
            open_positions=int(stats.get("open_positions", 0)),
            winning_trades=int(stats.get("winning_trades", 0)),
            losing_trades=int(stats.get("losing_trades", 0)),
            win_rate=float(stats.get("win_rate", 0.0)),
            expectancy=float(stats.get("expectancy", 0.0)),
            open_exposure_usd=open_exposure,
            realized_pnl_24h=realized_pnl_24h,
            biggest_win=biggest_win,
            cycles_24h=cycles_24h,
            failed_cycles_24h=failed_cycles_24h,
            latest_cycle_status=str(latest_cycle.status) if latest_cycle is not None else "n/a",
            latest_cycle_processed_news=int(latest_cycle.processed_news_count or 0)
            if latest_cycle is not None
            else 0,
            latest_cycle_actionable_signals=int(latest_cycle.actionable_signal_count or 0)
            if latest_cycle is not None
            else 0,
            latest_cycle_approved_signals=int(latest_cycle.approved_signal_count or 0)
            if latest_cycle is not None
            else 0,
            latest_cycle_opened_positions=int(latest_cycle.opened_position_count or 0)
            if latest_cycle is not None
            else 0,
            latest_cycle_closed_positions=int(latest_cycle.closed_position_count or 0)
            if latest_cycle is not None
            else 0,
            latest_cycle_errors=int(latest_cycle.error_count or 0) if latest_cycle is not None else 0,
            recent_trades=[self._trade_row_from_model(trade) for trade in recent_trades],
            open_position_rows=[
                self._position_row_from_model(position=position, now=now)
                for position in open_positions[:8]
            ],
            top_win_rows=[self._trade_row_from_model(trade) for trade in top_wins],
            top_loss_rows=[self._trade_row_from_model(trade) for trade in top_losses],
            recent_cycle_rows=[
                DashboardCycleRow(
                    cycle_id=str(cycle.cycle_id),
                    status=str(cycle.status),
                    processed_news=int(cycle.processed_news_count or 0),
                    actionable_signals=int(cycle.actionable_signal_count or 0),
                    approved_signals=int(cycle.approved_signal_count or 0),
                    opened_positions=int(cycle.opened_position_count or 0),
                    errors=int(cycle.error_count or 0),
                )
                for cycle in recent_cycles
            ],
            recent_cycle_ids=[cycle.cycle_id for cycle in recent_cycles],
        )
        self._manual_refresh_requested = False

    def cycle_started(self, *, cycle_number: int, cycle_id: str) -> None:
        self.status = "RUNNING"
        self.cycle_number = cycle_number
        self.active_cycle_id = cycle_id
        self.next_run_at = None
        self.log("CYCLE", f"cycle {cycle_number} started ({cycle_id})", style="cyan")

    def cycle_completed(self, result: SchedulerCycleResult) -> None:
        self.status = "IDLE"
        self.latest_result = result
        self.last_error = None
        self.active_cycle_id = None
        self.log(
            "DONE",
            (
                f"processed={result.processed_news_count} actionable={result.actionable_signal_count} "
                f"approved={result.approved_signal_count} opened={result.opened_position_count} "
                f"errors={result.error_count}"
            ),
            style="green" if result.error_count == 0 else "yellow",
        )

    def cycle_failed(self, *, cycle_number: int, error: str) -> None:
        self.status = "ERROR"
        self.last_error = error
        self.active_cycle_id = None
        self.log("FAIL", f"cycle {cycle_number} failed: {error}", style="bold red")

    def set_sleep(self, *, next_run_at: datetime) -> None:
        self.status = "SLEEPING"
        self.next_run_at = next_run_at

    def log(self, tag: str, message: str, *, style: str = "white") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(DashboardLogLine(timestamp, tag, message, style))
        self.refresh()

    def refresh(self) -> None:
        self._process_key_events()
        if self._live is not None:
            self._live.update(self.render())

    def render(self):
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="stats", size=5),
            Layout(name="body", ratio=1),
            Layout(name="terminal", size=12),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="main", ratio=12),
            Layout(name="sidecar", ratio=8),
        )
        layout["sidecar"].split_column(
            Layout(name="cycles", size=11),
            Layout(name="leaders", size=14),
            Layout(name="engine"),
        )

        layout["header"].update(self._build_header())
        layout["stats"].update(self._build_stats())
        layout["main"].update(self._build_main_panel())
        layout["cycles"].update(self._build_cycles_panel())
        layout["leaders"].update(self._build_leaders_panel())
        layout["engine"].update(self._build_engine_panel())
        layout["terminal"].update(self._build_terminal_panel())
        layout["footer"].update(self._build_footer())
        return layout

    def _build_header(self):
        now_text = datetime.now().strftime("%H:%M:%S")
        status_style = {
            "RUNNING": "bold green",
            "SLEEPING": "yellow",
            "ERROR": "bold red",
        }.get(self.status, "white")

        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right")

        left = Text()
        left.append("● ", style="white")
        left.append("POLYMARKET", style="bold white")
        left.append("  |  ", style="dim")
        left.append(self.brand_name, style="bold yellow")
        left.append("  |  ", style="dim")
        left.append(
            urlparse(self.settings.clob_api_base_url).netloc or "clob.polymarket.com",
            style="bright_blue",
        )
        left.append("  |  ", style="dim")
        left.append("Polygon", style="grey70")
        left.append("  |  ", style="dim")
        left.append(self.status, style=status_style)

        right = Text()
        right.append(now_text, style="bold white")
        if self.next_run_at is not None:
            right.append("  next ", style="dim")
            right.append(self._format_countdown(self.next_run_at), style="yellow")

        header.add_row(left, right)
        return Panel(header, border_style="bright_black", box=box.SQUARE)

    def _build_stats(self):
        grid = Table.grid(expand=True)
        for _ in range(5):
            grid.add_column(ratio=1)

        grid.add_row(
            self._stat_block(
                "ACCOUNT",
                self.account_name,
                f"{self.settings.news_fetch_mode.lower()} | {self.settings.llm_mode.lower()} | {self.settings.market_fetch_mode.lower()}",
            ),
            self._stat_block(
                "PROFIT/LOSS",
                self._format_money(self.snapshot.total_pnl),
                "all-time",
                value_style="bold green" if self.snapshot.total_pnl >= 0 else "bold red",
            ),
            self._stat_block(
                "PREDICTIONS",
                str(self.snapshot.total_trades),
                f"{self.snapshot.closed_trades} closed | {self.snapshot.open_positions} open",
            ),
            self._stat_block(
                "POSITIONS",
                self._format_money(self.snapshot.open_exposure_usd),
                f"{self.snapshot.open_positions} active | interval {self.interval_minutes:g}m",
            ),
            self._stat_block(
                "BIGGEST WIN",
                self._format_money(self.snapshot.biggest_win),
                f"win {self.snapshot.win_rate * 100:.1f}% | exp {self._format_money(self.snapshot.expectancy)}",
                value_style="bold yellow",
            ),
        )
        return Panel(grid, border_style="bright_black", box=box.SQUARE)

    def _build_main_panel(self):
        if self.main_view == "OPEN":
            return self._build_open_positions_main_panel()
        return self._build_closed_trades_panel()

    def _build_closed_trades_panel(self):
        table = Table(
            expand=True,
            border_style="bright_black",
            box=box.SIMPLE_HEAVY,
            header_style="grey70",
            pad_edge=False,
        )
        table.add_column("MARKET", overflow="fold", ratio=2)
        table.add_column("PICK", no_wrap=True)
        table.add_column("ENTRY", justify="right", style="cyan", no_wrap=True)
        table.add_column("ROI", justify="right", style="bright_blue", no_wrap=True)
        table.add_column("WON", justify="right", no_wrap=True)

        rows = self._filtered_trade_rows(self.snapshot.recent_trades or [])
        if not rows:
            table.add_row(self._empty_state("closed trades"), "-", "-", "-", "-")
        else:
            for trade in rows[:10]:
                pnl_style = self._pnl_style(trade.pnl)
                table.add_row(
                    self._shorten_market(trade.market, width=36),
                    Text(trade.pick, style=self._pick_style(trade.pick)),
                    self._format_cents(trade.entry_price),
                    self._format_roi(trade.pnl, trade.size_usd),
                    Text(self._format_money(trade.pnl or 0.0), style=pnl_style)
                    if trade.pnl is not None
                    else Text(trade.status, style="yellow"),
                )

        return Panel(
            table,
            title=self._panel_title("PENNY SNIPES"),
            subtitle=self._panel_tabs(active="CLOSED", secondary="OPEN"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_open_positions_main_panel(self):
        table = Table(
            expand=True,
            border_style="bright_black",
            box=box.SIMPLE_HEAVY,
            header_style="grey70",
            pad_edge=False,
        )
        table.add_column("MARKET", overflow="fold", ratio=2)
        table.add_column("PICK", no_wrap=True)
        table.add_column("ENTRY", justify="right", style="cyan", no_wrap=True)
        table.add_column("SIZE", justify="right", no_wrap=True)
        table.add_column("HOLD", justify="right", no_wrap=True)

        rows = self._filtered_position_rows(self.snapshot.open_position_rows or [])
        if not rows:
            table.add_row(self._empty_state("open positions"), "-", "-", "-", "-")
        else:
            for position in rows[:8]:
                table.add_row(
                    self._shorten_market(position.market, width=30),
                    Text(position.side, style=self._pick_style(position.side)),
                    self._format_cents(position.entry_price),
                    self._format_money(position.size_usd),
                    self._format_holding(position.holding_minutes),
                )

        return Panel(
            table,
            title=self._panel_title("PENNY SNIPES"),
            subtitle=self._panel_tabs(active="OPEN", secondary="CLOSED"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_leaders_panel(self):
        wins = self._build_leader_table(
            "TOP WINS",
            self._filtered_trade_rows(self.snapshot.top_win_rows or []),
            positive=True,
        )
        losses = self._build_leader_table(
            "TOP LOSSES",
            self._filtered_trade_rows(self.snapshot.top_loss_rows or []),
            positive=False,
        )
        return Panel(
            Group(wins, Text(""), losses),
            title=self._panel_title("SCOREBOARD"),
            subtitle=Text(f"filter {self.category_filter.lower()}", style="grey70"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_leader_table(
        self,
        label: str,
        rows: list[DashboardTradeRow],
        *,
        positive: bool,
    ):
        table = Table(
            expand=True,
            border_style="bright_black",
            box=box.SIMPLE_HEAVY,
            header_style="grey70",
            pad_edge=False,
        )
        table.add_column(label, ratio=2)
        table.add_column("PICK", no_wrap=True)
        table.add_column("PNL", justify="right", no_wrap=True)

        filtered = [
            row
            for row in rows
            if row.pnl is not None and ((row.pnl >= 0) if positive else (row.pnl < 0))
        ]
        if not filtered:
            table.add_row(self._empty_state("wins" if positive else "losses"), "-", "-")
            return table

        for trade in filtered[:3]:
            table.add_row(
                self._shorten_market(trade.market, width=20),
                Text(trade.pick, style=self._pick_style(trade.pick)),
                Text(self._format_money(trade.pnl or 0.0), style=self._pnl_style(trade.pnl)),
            )
        return table

    def _build_cycles_panel(self):
        table = Table(
            expand=True,
            border_style="bright_black",
            box=box.SIMPLE_HEAVY,
            header_style="grey70",
            pad_edge=False,
        )
        table.add_column("ID", no_wrap=True)
        table.add_column("ST", no_wrap=True)
        table.add_column("PROC", justify="right", no_wrap=True)
        table.add_column("APR", justify="right", no_wrap=True)
        table.add_column("OPEN", justify="right", no_wrap=True)
        table.add_column("ERR", justify="right", no_wrap=True)

        rows = self.snapshot.recent_cycle_rows or []
        if not rows:
            table.add_row("none", "-", "-", "-", "-", "-")
        else:
            for cycle in rows:
                table.add_row(
                    self._short_cycle(cycle.cycle_id),
                    Text(cycle.status[:4].upper(), style=self._cycle_status_style(cycle.status)),
                    str(cycle.processed_news),
                    str(cycle.approved_signals),
                    str(cycle.opened_positions),
                    str(cycle.errors),
                )

        return Panel(
            table,
            title=self._panel_title("RECENT CYCLES"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_engine_panel(self):
        return Panel(
            Syntax(self._build_engine_source(), "python", theme="monokai", line_numbers=True),
            title=self._panel_title("SOURCE"),
            subtitle=Text(self.source_file, style="bright_blue"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_terminal_panel(self):
        if not self.log_lines:
            return Panel(
                Text("No runtime events yet.", style="dim"),
                title=self._panel_title("TERMINAL"),
                border_style="bright_black",
                box=box.SQUARE,
            )

        rendered = []
        for line in list(self.log_lines)[-9:]:
            text = Text()
            text.append(f"[{line.timestamp}] ", style="dim")
            line_style = self._terminal_style(line.tag, line.style)
            text.append(f"{line.tag:<5}", style=line_style)
            text.append(" ")
            text.append(line.message, style=line_style)
            rendered.append(text)

        return Panel(
            Group(*rendered),
            title=self._panel_title("TERMINAL"),
            border_style="bright_black",
            box=box.SQUARE,
        )

    def _build_footer(self):
        command = Text()
        command.append(f"{self.account_name}@polymarket:~$ ", style="bold green")
        command.append("./penny_sniper ", style="bold white")
        command.append(f"--view={self.main_view.lower()} ", style="yellow")
        command.append(f"--filter={self.category_filter.lower()} ", style="yellow")
        command.append(f"--interval={self.interval_minutes:g}m ", style="yellow")
        command.append(f"--source={self.settings.news_fetch_mode.lower()} ", style="yellow")
        command.append(f"--llm={self.settings.llm_mode.lower()}", style="yellow")

        status = Text()
        status.append("ACCOUNT: ", style="dim")
        status.append(self.account_name, style="bold white")
        status.append("   PROFIT: ", style="dim")
        status.append(
            self._format_money(self.snapshot.total_pnl),
            style="bold green" if self.snapshot.total_pnl >= 0 else "bold red",
        )
        status.append("   PREDICTIONS: ", style="dim")
        status.append(str(self.snapshot.total_trades), style="bold white")
        status.append("   OPEN: ", style="dim")
        status.append(str(self.snapshot.open_positions), style="bold white")
        status.append("   FILTER: ", style="dim")
        status.append(self.category_filter.lower(), style="bold yellow")
        status.append("   WIN RATE: ", style="dim")
        status.append(f"{self.snapshot.win_rate * 100:.1f}%", style="bold cyan")
        status.append("   24H CYCLES: ", style="dim")
        status.append(str(self.snapshot.cycles_24h), style="bold white")
        status.append("   [f] ", style="dim")
        status.append("toggle", style="bold cyan")
        status.append("   [c] ", style="dim")
        status.append("filter", style="bold yellow")
        status.append("   [r] ", style="dim")
        status.append("refresh", style="bold cyan")
        status.append("   [q] ", style="dim")
        status.append("quit", style="bold cyan")
        status.append("   conn", style="dim")
        status.append(" ●", style="bold green" if self.status != "ERROR" else "bold red")

        return Panel(Group(command, status), border_style="bright_black", box=box.SQUARE)

    def _trade_row_from_model(self, trade: PaperTrade) -> DashboardTradeRow:
        market = None
        if trade.position is not None:
            market = trade.position.market_question
        if not market and trade.signal is not None and trade.signal.market_question:
            market = trade.signal.market_question
        if not market:
            market = trade.market_id

        return DashboardTradeRow(
            market=str(market),
            pick=str(trade.side.value),
            entry_price=float(trade.entry_price),
            size_usd=float(trade.size_usd),
            pnl=float(trade.pnl) if trade.pnl is not None else None,
            status=str(trade.status.value),
        )

    def _position_row_from_model(
        self,
        *,
        position: Position,
        now: datetime,
    ) -> DashboardPositionRow:
        holding_minutes = max((now - position.opened_at).total_seconds() / 60, 0.0)
        return DashboardPositionRow(
            market=str(position.market_question or position.market_id),
            side=str(position.side.value),
            entry_price=float(position.entry_price),
            size_usd=float(position.size_usd),
            holding_minutes=holding_minutes,
        )

    def _build_engine_source(self) -> str:
        return "\n".join(
            [
                "scanner = MarketScanner()",
                "matcher = DomainRanker()",
                "risk = RiskManager()",
                "paper = PaperTrader()",
                f"totalWins = {self.snapshot.winning_trades}",
                "",
                "def scan_all_markets():",
                f'    feed = "{self.settings.news_fetch_mode.lower()}"',
                f'    llm = "{self.settings.llm_mode.lower()}"',
                f'    market = "{self.settings.market_fetch_mode.lower()}"',
                f"    batch = {self.settings.scheduler_news_batch_limit}",
                '    domains = ["crypto", "macro",',
                '               "geopolitical", "sports"]',
                "",
                "def on_cycle_end():",
                "    latest = {",
                f'        "status": "{self.snapshot.latest_cycle_status}",',
                f'        "processed": {self.snapshot.latest_cycle_processed_news},',
                f'        "approved": {self.snapshot.latest_cycle_approved_signals},',
                f'        "opened": {self.snapshot.latest_cycle_opened_positions},',
                f'        "closed": {self.snapshot.latest_cycle_closed_positions},',
                f'        "errors": {self.snapshot.latest_cycle_errors},',
                "    }",
                "    return latest",
            ]
        )

    def _stat_block(
        self,
        label: str,
        value: str,
        meta: str,
        *,
        value_style: str = "bold white",
    ):
        text = Text()
        text.append(label, style="dim")
        text.append("\n")
        text.append(value, style=value_style)
        text.append("\n")
        text.append(meta, style="dim")
        return text

    def _latest_cycle_label(self) -> str:
        if self.latest_result is not None:
            value = self.latest_result.cycle_id
        else:
            value = self.active_cycle_id or self.snapshot.latest_cycle_status
        if len(value) <= 18:
            return value
        return value[:15] + "..."

    def _latest_cycle_meta(self) -> str:
        return (
            f"proc {self.snapshot.latest_cycle_processed_news} | "
            f"apr {self.snapshot.latest_cycle_approved_signals} | "
            f"open {self.snapshot.latest_cycle_opened_positions} | "
            f"err {self.snapshot.latest_cycle_errors}"
        )

    def take_manual_refresh_request(self) -> bool:
        requested = self._manual_refresh_requested
        self._manual_refresh_requested = False
        return requested

    def _pnl_style(self, pnl: float | None) -> str:
        if pnl is None:
            return "yellow"
        if pnl > 0:
            return "bold green"
        if pnl < 0:
            return "bold red"
        return "white"

    def _pick_style(self, pick: str) -> str:
        normalized = pick.upper()
        if normalized in {"YES", "UP", "LONG"}:
            return "bold green"
        if normalized in {"NO", "DOWN", "SHORT"}:
            return "bold red"
        if normalized in {"ZERO", "DRAW"}:
            return "bold cyan"
        if normalized == "OMG":
            return "bold magenta"
        return "bold yellow"

    def _format_money(self, value: float | Decimal) -> str:
        amount = float(value)
        sign = "-" if amount < 0 else ""
        return f"{sign}${abs(amount):,.2f}"

    def _format_cents(self, value: float) -> str:
        cents = value * 100
        precision = 1 if cents >= 10 else 2
        numeric = f"{cents:.{precision}f}".rstrip("0").rstrip(".")
        return f"{numeric}c"

    def _format_roi(self, pnl: float | None, size_usd: float) -> str:
        if pnl is None or size_usd <= 0:
            return "--"
        return f"{(pnl / size_usd) * 100:+,.1f}%"

    def _format_countdown(self, target: datetime | None) -> str:
        if target is None:
            return "--:--"
        remaining = max(int((target - datetime.now(UTC)).total_seconds()), 0)
        minutes, seconds = divmod(remaining, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _format_holding(self, holding_minutes: float) -> str:
        total_minutes = int(max(holding_minutes, 0))
        hours, minutes = divmod(total_minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def _shorten_market(self, value: str, *, width: int) -> str:
        text = value.strip()
        if len(text) <= width:
            return text
        return text[: width - 3].rstrip() + "..."

    def _short_cycle(self, cycle_id: str) -> str:
        if len(cycle_id) <= 12:
            return cycle_id
        return cycle_id[9:15]

    def _cycle_status_style(self, status: str) -> str:
        normalized = status.upper()
        if normalized == "FAILED":
            return "bold red"
        if normalized == "COMPLETED":
            return "bold green"
        if normalized in {"STARTED", "RUNNING"}:
            return "bold yellow"
        return "bold white"

    def _panel_title(self, label: str) -> Text:
        title = Text()
        title.append("▶ ", style="grey50")
        title.append(label, style="bold white")
        if label == "PENNY SNIPES":
            title.append("  -  ", style="grey35")
            title.append(self.account_name, style="grey70")
        return title

    def _panel_tabs(self, *, active: str, secondary: str) -> Text:
        title = Text()
        title.append(active, style="bold cyan")
        title.append("  ", style="dim")
        title.append(secondary, style="grey50")
        title.append("  |  ", style="dim")
        title.append(self.category_filter.lower(), style="bold yellow")
        return title

    def _filtered_trade_rows(self, rows: list[DashboardTradeRow]) -> list[DashboardTradeRow]:
        if self.category_filter == "ALL":
            return rows
        return [row for row in rows if self._market_category(row.market) == self.category_filter]

    def _filtered_position_rows(
        self,
        rows: list[DashboardPositionRow],
    ) -> list[DashboardPositionRow]:
        if self.category_filter == "ALL":
            return rows
        return [row for row in rows if self._market_category(row.market) == self.category_filter]

    def _empty_state(self, subject: str) -> str:
        if self.category_filter == "ALL":
            return f"No {subject} yet"
        return f"No {self.category_filter.lower()} {subject}"

    def _market_category(self, market: str) -> str:
        value = market.lower()
        crypto_tokens = (
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "xrp",
            "crypto",
            "doge",
            "cardano",
            "stablecoin",
            "token",
            "altcoin",
            "blockchain",
        )
        sports_tokens = (
            " vs ",
            "match",
            "game ",
            "bo3",
            "bo5",
            "series",
            "dota",
            "lol",
            "esl",
            "atp",
            "wta",
            "open sud",
            "team ",
            "fc ",
            "nba",
            "nfl",
            "nhl",
            "mlb",
            "epl",
            "tennis",
        )
        macro_tokens = (
            "fed",
            "rate",
            "rates",
            "cpi",
            "inflation",
            "gdp",
            "recession",
            "treasury",
            "yield",
            "oil",
            "s&p",
            "nasdaq",
            "dow",
            "economy",
            "stocks",
            "tariff",
        )
        politics_tokens = (
            "trump",
            "election",
            "president",
            "senate",
            "house",
            "congress",
            "republican",
            "democrat",
            "ambassador",
            "israel",
            "syria",
            "ukraine",
            "war",
            "government",
            "minister",
            "prime minister",
        )

        if any(token in value for token in crypto_tokens):
            return "CRYPTO"
        if any(token in value for token in sports_tokens):
            return "SPORTS"
        if any(token in value for token in macro_tokens):
            return "MACRO"
        if any(token in value for token in politics_tokens):
            return "POLITICS"
        return "OTHER"

    def _cycle_category_filter(self) -> None:
        filters = ("ALL", "CRYPTO", "MACRO", "POLITICS", "SPORTS", "OTHER")
        current_index = filters.index(self.category_filter)
        self.category_filter = filters[(current_index + 1) % len(filters)]

    def _terminal_style(self, tag: str, fallback: str) -> str:
        normalized = tag.upper()
        if normalized in {"WIN", "OPEN"}:
            return "bold green"
        if normalized in {"LOSS", "FAIL"}:
            return "bold red"
        if normalized in {"BLOCK", "SKIP"}:
            return "bold yellow"
        if normalized in {"MATCH", "FETCH"}:
            return "bold cyan"
        if normalized in {"SIGNL", "TAB"}:
            return "bold magenta"
        if normalized in {"DONE", "ITEM"}:
            return "bold white"
        return fallback

    def _process_key_events(self) -> None:
        while True:
            try:
                key = self._key_queue.get_nowait()
            except Empty:
                break

            normalized = key.lower()
            if normalized == "q":
                self.exit_requested = True
                self.log("QUIT", "shutdown requested from keyboard", style="yellow")
            elif normalized == "r":
                self._manual_refresh_requested = True
                self.log("REFSH", "manual refresh requested", style="cyan")
            elif normalized == "f":
                self.main_view = "OPEN" if self.main_view == "CLOSED" else "CLOSED"
                self.log("TAB", f"switched to {self.main_view.lower()} view", style="magenta")
            elif normalized == "c":
                self._cycle_category_filter()
                self.log("FILT", f"market filter -> {self.category_filter.lower()}", style="yellow")

    @contextmanager
    def _keyboard_listener(self):
        if not sys.stdin.isatty() or "termios" not in globals() or "tty" not in globals():
            yield
            return

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        self._keyboard_stop.clear()

        def _reader() -> None:
            while not self._keyboard_stop.is_set():
                readable, _, _ = select.select([fd], [], [], 0.1)
                if not readable:
                    continue
                char = sys.stdin.read(1)
                if char:
                    self._key_queue.put(char)

        try:
            tty.setcbreak(fd)
            self._keyboard_thread = threading.Thread(target=_reader, daemon=True)
            self._keyboard_thread.start()
            yield
        finally:
            self._keyboard_stop.set()
            if self._keyboard_thread is not None:
                self._keyboard_thread.join(timeout=0.5)
                self._keyboard_thread = None
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
