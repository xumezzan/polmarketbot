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
