"""Provider registry. Add adapters without changing Home Assistant platforms."""

from collections.abc import Callable
from typing import Protocol

from aiohttp import ClientSession

from ..models import Snapshot
from .eon import EonClient


class ProviderClient(Protocol):
    async def async_fetch(self) -> Snapshot:
        """Fetch normalized outage feeds, sharing requests between addresses."""


PROVIDERS: dict[str, Callable[[ClientSession], ProviderClient]] = {"eon": EonClient}
