import hashlib
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Protocol

from app.config import Settings
from app.logging_utils import log_event
from app.models.enums import (
    ExecutionIntentStatus,
    ExecutionMode,
    LiveOrderStatus,
    MarketSide,
    VerdictDirection,
)
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.execution_intent_repo import ExecutionIntentRepository
from app.repositories.live_trade_repo import LiveTradeRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.signal_repo import SignalRepository
from app.runtime_flags import RUNTIME_FLAG_LIVE_CIRCUIT_BREAKER, RUNTIME_FLAG_LIVE_TRADING_KILL_SWITCH
from app.schemas.live_execution import (
    ExecutionIntentPayload,
    ExecutionIntentRecord,
    LiveOrderResult,
    ReconciliationResult,
    ShadowExecutionResult,
)
from app.services.market_client import MarketClientProtocol, build_market_client


logger = logging.getLogger(__name__)


class LiveExecutionError(Exception):
    """Raised when shadow/live execution cannot proceed."""


class LiveTradingDisabledError(LiveExecutionError):
    """Raised when live trading is disabled by config."""


class LiveKillSwitchEnabledError(LiveExecutionError):
    """Raised when live kill switch is enabled."""


class CircuitBreakerTriggeredError(LiveExecutionError):
    """Raised when circuit breaker blocks live execution."""


class CLOBClientProtocol(Protocol):
    """Minimal CLOB adapter contract used by the bot."""

    def create_order(self, *, token_id: str, price: float, size: float, side: str) -> dict[str, object]:
        """Return signed order payload."""

    def post_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str,
    ) -> dict[str, object]:
        """Submit one signed order."""

    def get_order_status(self, *, order_id: str) -> dict[str, object]:
        """Return exchange order snapshot."""

    def cancel_order(self, *, order_id: str) -> dict[str, object]:
        """Cancel one order by exchange order id."""

    def get_open_orders(self, *, market_id: str | None = None) -> list[dict[str, object]]:
        """Return authenticated list of currently open orders."""


class StubCLOBClient:
    """Deterministic fake client for shadow-mode tests."""

    def create_order(self, *, token_id: str, price: float, size: float, side: str) -> dict[str, object]:
        return {
            "token_id": token_id,
            "price": round(price, 4),
            "size": round(size, 6),
            "side": side,
        }

    def post_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str,
    ) -> dict[str, object]:
        digest = hashlib.sha256(
            f"{token_id}:{price:.4f}:{size:.6f}:{side}:{order_type}".encode("utf-8")
        ).hexdigest()
        return {
            "success": True,
            "orderID": f"stub-{digest[:16]}",
            "status": "filled",
            "tokenID": token_id,
            "price": round(price, 4),
            "size": round(size, 6),
            "side": side,
            "orderType": order_type,
        }

    def get_order_status(self, *, order_id: str) -> dict[str, object]:
        return {"orderID": order_id, "status": "filled"}

    def cancel_order(self, *, order_id: str) -> dict[str, object]:
        return {"canceled": True, "orderID": order_id}

    def get_open_orders(self, *, market_id: str | None = None) -> list[dict[str, object]]:
        return []


class PolymarketCLOBClient:
    """Thin adapter over py-clob-client-v2."""

    def __init__(self, settings: Settings) -> None:
        try:
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds
        except ImportError as exc:
            raise LiveExecutionError("py-clob-client-v2 is not installed") from exc

        if not settings.clob_private_key:
            raise LiveExecutionError("CLOB_PRIVATE_KEY is required for live mode")

        creds = None
        if (
            settings.clob_api_key
            and settings.clob_api_secret
            and settings.clob_api_passphrase
        ):
            creds = ApiCreds(
                api_key=settings.clob_api_key,
                api_secret=settings.clob_api_secret,
                api_passphrase=settings.clob_api_passphrase,
            )

        self._settings = settings
        self._client = ClobClient(
            host=settings.clob_api_base_url.rstrip("/"),
            chain_id=settings.clob_chain_id,
            key=settings.clob_private_key,
            creds=creds,
            signature_type=settings.clob_signature_type,
            funder=settings.clob_funder or None,
        )
        if creds is None:
            derived = self._client.create_or_derive_api_key()
            self._client.set_api_creds(derived)

    def create_order(self, *, token_id: str, price: float, size: float, side: str) -> dict[str, object]:
        from py_clob_client_v2.clob_types import OrderArgsV2

        order = self._client.create_order(
            OrderArgsV2(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 6),
                side=side,
            )
        )
        if hasattr(order, "__dataclass_fields__"):
            return asdict(order)
        if hasattr(order, "__dict__"):
            return dict(order.__dict__)
        return {"repr": str(order)}

    def post_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str,
    ) -> dict[str, object]:
        from py_clob_client_v2.clob_types import OrderArgsV2

        return self._client.create_and_post_order(
            OrderArgsV2(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 6),
                side=side,
            ),
            order_type=order_type,
        )

    def get_order_status(self, *, order_id: str) -> dict[str, object]:
        return self._client.get_order(order_id)

    def cancel_order(self, *, order_id: str) -> dict[str, object]:
        from py_clob_client_v2.clob_types import OrderPayload

        return self._client.cancel_order(OrderPayload(orderID=order_id))

    def get_open_orders(self, *, market_id: str | None = None) -> list[dict[str, object]]:
        from py_clob_client_v2.clob_types import OpenOrderParams

        params = OpenOrderParams(market=market_id) if market_id is not None else None
        return self._client.get_open_orders(params=params, only_first_page=False)


def _normalize_execution_mode(raw_mode: str) -> ExecutionMode:
    normalized = raw_mode.strip().upper()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        raise LiveExecutionError(f"Unsupported EXECUTION_MODE={raw_mode}") from exc


def _normalize_order_type(raw_order_type: str) -> str:
    normalized = raw_order_type.strip().upper()
    if normalized not in {"GTC", "FOK", "GTD", "FAK"}:
        raise LiveExecutionError(f"Unsupported LIVE_ORDER_TYPE={raw_order_type}")
    return normalized


def _compute_side_and_token(signal, market) -> tuple[MarketSide, str]:
    direction = signal.analysis.direction
    if direction == VerdictDirection.YES:
        token_id = market.yes_token_id
        side = MarketSide.YES
    elif direction == VerdictDirection.NO:
        token_id = market.no_token_id
        side = MarketSide.NO
    else:
        raise LiveExecutionError(f"Unsupported signal direction for execution: {direction.value}")

    if not token_id:
        raise LiveExecutionError(f"Market {market.id} is missing token id for side={side.value}")
    return side, token_id


def _select_requested_price(signal, market, *, side: MarketSide) -> float:
    if signal.execution_price is not None:
        return round(float(signal.execution_price), 4)
    if side == MarketSide.YES and market.yes_price is not None:
        return round(float(market.yes_price), 4)
    if side == MarketSide.NO and market.no_price is not None:
        return round(float(market.no_price), 4)
    if signal.market_price is not None:
        return round(float(signal.market_price), 4)
    raise LiveExecutionError(f"Signal {signal.id} has no executable price")


def _build_client_order_id(
    *,
    signal_id: int,
    execution_mode: ExecutionMode,
    token_id: str,
    requested_price: float,
    shares: float,
) -> str:
    digest = hashlib.sha256(
        f"{signal_id}:{execution_mode.value}:{token_id}:{requested_price:.4f}:{shares:.6f}".encode(
            "utf-8"
        )
    ).hexdigest()
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{execution_mode.value.lower()}-{signal_id}-{digest[:8]}-{timestamp}"


def build_clob_client(settings: Settings) -> CLOBClientProtocol:
    execution_mode = _normalize_execution_mode(settings.execution_mode)
    if execution_mode == ExecutionMode.SHADOW and not settings.clob_private_key:
        return StubCLOBClient()
    return PolymarketCLOBClient(settings)


class LiveExecutionService:
    """Separate execution stack for phase-2 shadow mode and phase-3 micro-live."""

    def __init__(
        self,
        *,
        settings: Settings,
        signal_repository: SignalRepository,
        analysis_repository: AnalysisRepository,
        runtime_flag_repository: RuntimeFlagRepository,
        execution_intent_repository: ExecutionIntentRepository,
        live_trade_repository: LiveTradeRepository,
        market_client: MarketClientProtocol,
        clob_client: CLOBClientProtocol,
    ) -> None:
        self.settings = settings
        self.signal_repository = signal_repository
        self.analysis_repository = analysis_repository
        self.runtime_flag_repository = runtime_flag_repository
        self.execution_intent_repository = execution_intent_repository
        self.live_trade_repository = live_trade_repository
        self.market_client = market_client
        self.clob_client = clob_client

    async def build_execution_intent(
        self,
        *,
        signal_id: int,
        approved_size_usd: float,
    ) -> tuple[ExecutionIntentRecord, object]:
        execution_mode = _normalize_execution_mode(self.settings.execution_mode)
        signal = await self.signal_repository.get_by_id(signal_id)
        if signal is None:
            raise LiveExecutionError(f"Signal {signal_id} not found")

        market = await self.market_client.fetch_market(signal.market_id)
        if market is None:
            raise LiveExecutionError(f"Market {signal.market_id} not found")

        side, token_id = _compute_side_and_token(signal, market)
        requested_price = _select_requested_price(signal, market, side=side)
        if requested_price <= 0:
            raise LiveExecutionError(f"Signal {signal.id} has invalid requested price {requested_price}")

        shares = round(approved_size_usd / requested_price, 6)
        if shares <= 0:
            raise LiveExecutionError(f"Signal {signal.id} resulted in zero shares")

        max_price = round(
            min(1.0, requested_price * (1 + (self.settings.live_price_buffer_bps / 10000))),
            4,
        )
        client_order_id = _build_client_order_id(
            signal_id=signal.id,
            execution_mode=execution_mode,
            token_id=token_id,
            requested_price=requested_price,
            shares=shares,
        )
        payload = ExecutionIntentPayload(
            asset_id=token_id,
            market_id=signal.market_id,
            side=side.value,
            target_size_usd=round(approved_size_usd, 2),
            shares=shares,
            requested_price=requested_price,
            max_acceptable_price=max_price,
            order_type=_normalize_order_type(self.settings.live_order_type),
            client_order_id=client_order_id,
        )
        return (
            ExecutionIntentRecord(
                intent_id=0,
                signal_id=signal.id,
                market_id=signal.market_id,
                side=side.value,
                token_id=token_id,
                execution_mode=execution_mode.value,
                status=ExecutionIntentStatus.SIMULATED.value,
                target_size_usd=round(approved_size_usd, 2),
                shares=shares,
                requested_price=requested_price,
                max_acceptable_price=max_price,
                client_order_id=client_order_id,
                payload=payload,
                created_at=datetime.now(UTC).isoformat(),
            ),
            signal,
        )

    async def simulate_execution(
        self,
        *,
        signal_id: int,
        approved_size_usd: float,
    ) -> ShadowExecutionResult:
        intent_record, signal = await self.build_execution_intent(
            signal_id=signal_id,
            approved_size_usd=approved_size_usd,
        )
        payload = intent_record.payload.model_dump()
        signed_preview = self.clob_client.create_order(
            token_id=intent_record.token_id,
            price=intent_record.requested_price,
            size=intent_record.shares,
            side="BUY",
        )
        audit_trail = [
            f"signal={signal.id}",
            f"market={intent_record.market_id}",
            f"side={intent_record.side}",
            f"size_usd={intent_record.target_size_usd:.2f}",
            f"price={intent_record.requested_price:.4f}",
        ]
        intent = await self.execution_intent_repository.create(
            signal_id=signal.id,
            market_id=intent_record.market_id,
            market_question=signal.market_question,
            side=MarketSide(intent_record.side),
            token_id=intent_record.token_id,
            execution_mode=ExecutionMode.SHADOW,
            status=ExecutionIntentStatus.SIMULATED,
            target_size_usd=intent_record.target_size_usd,
            shares=intent_record.shares,
            requested_price=intent_record.requested_price,
            max_acceptable_price=intent_record.max_acceptable_price,
            client_order_id=intent_record.client_order_id,
            generated_payload=payload,
            simulation_result={"signed_order_preview": signed_preview, "audit_trail": audit_trail},
        )
        await self.analysis_repository.save_execution_action(
            analysis_id=signal.analysis_id,
            action={
                "action": "shadow_execution_simulated",
                "action_at": datetime.now(UTC).isoformat(),
                "signal_id": signal.id,
                "intent_id": intent.id,
                "client_order_id": intent.client_order_id,
                "payload": payload,
                "signed_order_preview": signed_preview,
            },
        )
        return ShadowExecutionResult(
            intent=intent_record.model_copy(
                update={
                    "intent_id": intent.id,
                    "status": intent.status.value,
                    "created_at": intent.created_at.isoformat(),
                }
            ),
            audit_trail=audit_trail,
        )

    async def place_order(
        self,
        *,
        signal_id: int,
        approved_size_usd: float,
    ) -> LiveOrderResult:
        await self._assert_live_allowed(approved_size_usd=approved_size_usd)
        intent_record, signal = await self.build_execution_intent(
            signal_id=signal_id,
            approved_size_usd=approved_size_usd,
        )
        payload = intent_record.payload.model_dump()
        now = datetime.now(UTC)

        intent = await self.execution_intent_repository.create(
            signal_id=signal.id,
            market_id=intent_record.market_id,
            market_question=signal.market_question,
            side=MarketSide(intent_record.side),
            token_id=intent_record.token_id,
            execution_mode=ExecutionMode.LIVE,
            status=ExecutionIntentStatus.SIMULATED,
            target_size_usd=intent_record.target_size_usd,
            shares=intent_record.shares,
            requested_price=intent_record.requested_price,
            max_acceptable_price=intent_record.max_acceptable_price,
            client_order_id=intent_record.client_order_id,
            generated_payload=payload,
        )

        try:
            response = self.clob_client.post_order(
                token_id=intent_record.token_id,
                price=intent_record.requested_price,
                size=intent_record.shares,
                side="BUY",
                order_type=_normalize_order_type(self.settings.live_order_type),
            )
        except Exception as exc:
            error_text = str(exc)
            intent = await self.execution_intent_repository.mark_failed(
                intent=intent,
                error=error_text,
                simulation_result={"payload": payload},
            )
            order = await self.live_trade_repository.create_order(
                execution_intent_id=intent.id,
                signal_id=signal.id,
                market_id=intent.market_id,
                side=intent.side,
                token_id=intent.token_id,
                client_order_id=intent.client_order_id,
                exchange_order_id=None,
                requested_price=float(intent.requested_price),
                filled_price=None,
                size_usd=float(intent.target_size_usd),
                shares=float(intent.shares),
                status=LiveOrderStatus.FAILED,
                raw_request=payload,
                raw_response={"error": error_text},
                failure_reason=error_text,
            )
            await self.analysis_repository.save_execution_action(
                analysis_id=signal.analysis_id,
                action={
                    "action": "live_order_failed",
                    "action_at": now.isoformat(),
                    "signal_id": signal.id,
                    "intent_id": intent.id,
                    "live_order_id": order.id,
                    "error": error_text,
                },
            )
            raise LiveExecutionError(error_text) from exc

        exchange_order_id = self._extract_exchange_order_id(response)
        normalized_status = self._normalize_live_order_status(response)
        intent = await self.execution_intent_repository.mark_submitted(
            intent=intent,
            exchange_order_id=exchange_order_id,
            simulation_result={"exchange_response": response},
        )
        order = await self.live_trade_repository.create_order(
            execution_intent_id=intent.id,
            signal_id=signal.id,
            market_id=intent.market_id,
            side=intent.side,
            token_id=intent.token_id,
            client_order_id=intent.client_order_id,
            exchange_order_id=exchange_order_id,
            requested_price=float(intent.requested_price),
            filled_price=float(intent.requested_price) if normalized_status == LiveOrderStatus.FILLED else None,
            size_usd=float(intent.target_size_usd),
            shares=float(intent.shares),
            status=normalized_status,
            raw_request=payload,
            raw_response=response,
            failure_reason=self._extract_error(response),
        )
        position_id = None
        if normalized_status in {LiveOrderStatus.OPEN, LiveOrderStatus.FILLED}:
            position = await self.live_trade_repository.create_position(
                signal_id=signal.id,
                live_order_id=order.id,
                market_id=intent.market_id,
                market_question=signal.market_question,
                side=intent.side,
                token_id=intent.token_id,
                entry_price=float(intent.requested_price),
                size_usd=float(intent.target_size_usd),
                shares=float(intent.shares),
            )
            position_id = position.id

        await self.analysis_repository.save_execution_action(
            analysis_id=signal.analysis_id,
            action={
                "action": "live_order_submitted",
                "action_at": now.isoformat(),
                "signal_id": signal.id,
                "intent_id": intent.id,
                "live_order_id": order.id,
                "live_position_id": position_id,
                "exchange_order_id": exchange_order_id,
                "status": normalized_status.value,
                "response": response,
            },
        )
        log_event(
            logger,
            "live_order_submitted",
            signal_id=signal.id,
            intent_id=intent.id,
            live_order_id=order.id,
            live_position_id=position_id,
            exchange_order_id=exchange_order_id,
            status=normalized_status.value,
        )
        return LiveOrderResult(
            intent=intent_record.model_copy(
                update={
                    "intent_id": intent.id,
                    "status": intent.status.value,
                    "exchange_order_id": exchange_order_id,
                    "created_at": intent.created_at.isoformat(),
                    "executed_at": intent.executed_at.isoformat()
                    if intent.executed_at is not None
                    else None,
                }
            ),
            live_order_id=order.id,
            live_position_id=position_id,
            order_status=normalized_status.value,
            exchange_order_id=exchange_order_id,
            raw_response=response,
        )

    async def get_order_status(self, *, order_id: str) -> dict[str, object]:
        return self.clob_client.get_order_status(order_id=order_id)

    async def cancel_order(self, *, order_id: str) -> dict[str, object]:
        return self.clob_client.cancel_order(order_id=order_id)

    async def reconcile_open_orders(self) -> ReconciliationResult:
        open_positions = await self.live_trade_repository.list_open_positions()
        remote_open_orders = self.clob_client.get_open_orders()
        remote_ids = {
            str(item.get("id") or item.get("orderID") or item.get("order_id"))
            for item in remote_open_orders
            if item.get("id") or item.get("orderID") or item.get("order_id")
        }
        local_ids = {
            position.live_order.exchange_order_id
            for position in open_positions
            if position.live_order is not None and position.live_order.exchange_order_id
        }
        missing_on_exchange = sorted(local_ids - remote_ids)
        unknown_locally = sorted(remote_ids - local_ids)
        mismatch_count = len(missing_on_exchange) + len(unknown_locally)
        status = "PASSED" if mismatch_count == 0 else "MISMATCH"
        return ReconciliationResult(
            status=status,
            mismatch_count=mismatch_count,
            details={
                "missing_on_exchange": missing_on_exchange,
                "unknown_locally": unknown_locally,
            },
        )

    async def _assert_live_allowed(self, *, approved_size_usd: float) -> None:
        if not self.settings.live_trading_enabled:
            raise LiveTradingDisabledError("LIVE_TRADING_ENABLED=false")

        kill_switch_enabled = await self.runtime_flag_repository.get_bool(
            key=RUNTIME_FLAG_LIVE_TRADING_KILL_SWITCH,
            default=False,
        )
        if kill_switch_enabled:
            raise LiveKillSwitchEnabledError("live_trading_kill_switch=true")

        breaker_enabled = await self.runtime_flag_repository.get_bool(
            key=RUNTIME_FLAG_LIVE_CIRCUIT_BREAKER,
            default=False,
        )
        if breaker_enabled:
            raise CircuitBreakerTriggeredError("live_circuit_breaker=true")

        if approved_size_usd < self.settings.live_min_trade_size_usd:
            raise LiveExecutionError(
                f"approved_size_usd={approved_size_usd:.2f} below "
                f"LIVE_MIN_TRADE_SIZE_USD={self.settings.live_min_trade_size_usd:.2f}"
            )
        if approved_size_usd > self.settings.live_max_trade_size_usd:
            raise LiveExecutionError(
                f"approved_size_usd={approved_size_usd:.2f} exceeds "
                f"LIVE_MAX_TRADE_SIZE_USD={self.settings.live_max_trade_size_usd:.2f}"
            )

        open_positions = await self.live_trade_repository.count_open_positions()
        if open_positions >= self.settings.live_max_open_positions:
            raise LiveExecutionError(
                f"open_positions={open_positions} exceeds LIVE_MAX_OPEN_POSITIONS="
                f"{self.settings.live_max_open_positions}"
            )

        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_exposure = await self.live_trade_repository.sum_daily_exposure_used_usd(
            day_start=day_start
        )
        if daily_exposure + approved_size_usd > self.settings.live_max_daily_exposure_usd:
            raise LiveExecutionError(
                f"daily_exposure={daily_exposure + approved_size_usd:.2f} exceeds "
                f"LIVE_MAX_DAILY_EXPOSURE_USD={self.settings.live_max_daily_exposure_usd:.2f}"
            )

    def _extract_exchange_order_id(self, response: dict[str, object]) -> str | None:
        for key in ("orderID", "id", "order_id"):
            value = response.get(key)
            if value is not None:
                return str(value)
        return None

    def _extract_error(self, response: dict[str, object]) -> str | None:
        for key in ("errorMsg", "error", "message"):
            value = response.get(key)
            if value:
                return str(value)
        return None

    def _normalize_live_order_status(self, response: dict[str, object]) -> LiveOrderStatus:
        if self._extract_error(response):
            return LiveOrderStatus.FAILED

        raw_status = str(response.get("status") or response.get("state") or "").upper()
        if raw_status in {"FILLED", "MATCHED", "EXECUTED"}:
            return LiveOrderStatus.FILLED
        if raw_status in {"CANCELED", "CANCELLED"}:
            return LiveOrderStatus.CANCELED
        if raw_status in {"FAILED", "REJECTED"}:
            return LiveOrderStatus.FAILED
        if self._extract_exchange_order_id(response):
            return LiveOrderStatus.OPEN
        return LiveOrderStatus.FAILED


async def simulate_execution_intent(
    session,
    settings: Settings,
    *,
    signal_id: int,
    approved_size_usd: float,
) -> ShadowExecutionResult:
    service = LiveExecutionService(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        execution_intent_repository=ExecutionIntentRepository(session),
        live_trade_repository=LiveTradeRepository(session),
        market_client=build_market_client(settings),
        clob_client=build_clob_client(settings),
    )
    return await service.simulate_execution(
        signal_id=signal_id,
        approved_size_usd=approved_size_usd,
    )


async def place_live_order(
    session,
    settings: Settings,
    *,
    signal_id: int,
    approved_size_usd: float,
) -> LiveOrderResult:
    service = LiveExecutionService(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        execution_intent_repository=ExecutionIntentRepository(session),
        live_trade_repository=LiveTradeRepository(session),
        market_client=build_market_client(settings),
        clob_client=build_clob_client(settings),
    )
    return await service.place_order(
        signal_id=signal_id,
        approved_size_usd=approved_size_usd,
    )


async def reconcile_live_state(session, settings: Settings) -> ReconciliationResult:
    service = LiveExecutionService(
        settings=settings,
        signal_repository=SignalRepository(session),
        analysis_repository=AnalysisRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        execution_intent_repository=ExecutionIntentRepository(session),
        live_trade_repository=LiveTradeRepository(session),
        market_client=build_market_client(settings),
        clob_client=build_clob_client(settings),
    )
    return await service.reconcile_open_orders()
