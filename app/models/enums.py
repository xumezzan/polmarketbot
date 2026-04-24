from enum import Enum


class VerdictDirection(str, Enum):
    YES = "YES"
    NO = "NO"
    NONE = "NONE"


class SignalStatus(str, Enum):
    REJECTED = "REJECTED"
    WATCHLIST = "WATCHLIST"
    ACTIONABLE = "ACTIONABLE"


class MarketSide(str, Enum):
    YES = "YES"
    NO = "NO"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class ExecutionIntentStatus(str, Enum):
    SIMULATED = "SIMULATED"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class LiveOrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


class LivePositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ReconciliationStatus(str, Enum):
    PASSED = "PASSED"
    MISMATCH = "MISMATCH"
    FAILED = "FAILED"
