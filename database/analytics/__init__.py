from .audience import audience
from .audit import audit
from .base import StatsCtx
from .overview import overview
from .retention import retention
from .revenue import revenue


DOMAINS = {
    "overview": overview,
    "revenue": revenue,
    "retention": retention,
    "audience": audience,
    "audit": audit,
}

__all__ = ["DOMAINS", "StatsCtx"]
