"""E.ON Hungary public website feed adapter. No address is sent upstream."""

import asyncio
import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession, ClientTimeout

from ..const import MIN_REFRESH_SECONDS
from ..models import AddressRange, FaultReport, Feed, PlannedOutage, Point, Snapshot

BASE_URL = "https://www.eon.hu/content/dam/eon/eon-hungary/external-app-data/outages/"
PLANNED_URL = BASE_URL + "poweroutage.json"
FAULTS_URL = BASE_URL + "unexpectedoutage.json"
BUDAPEST = ZoneInfo("Europe/Budapest")
MAX_BYTES = 10 * 1024 * 1024


class FeedError(ValueError):
    """Feed changed or contains invalid data; never treat this as an empty feed."""


def timestamp(value: dict) -> datetime:
    if not isinstance(value, dict):
        raise FeedError("Invalid timestamp")
    try:
        return datetime.fromisoformat(value["date"] + "T" + value["time"]).replace(tzinfo=BUDAPEST)
    except (KeyError, TypeError, ValueError) as err:
        raise FeedError("Invalid timestamp") from err


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def point(value: object) -> Point | None:
    """Accept only finite marker coordinates in a loose Hungary bounding box."""
    if not isinstance(value, dict):
        return None
    try:
        lat, lon = value["lat"], value["lng"]
        if isinstance(lat, bool) or isinstance(lon, bool):
            return None
        lat, lon = float(lat), float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and 45.5 <= lat <= 49 and 15.5 <= lon <= 23.5:
            return Point(lat, lon)
    except KeyError, ValueError, TypeError:
        pass
    return None


def area_radius(value: object) -> float:
    """E.ON passes this field to Google Maps Circle, whose radius is metres."""
    if isinstance(value, bool):
        return 0
    try:
        radius = float(value)
        return radius if math.isfinite(radius) and 0 < radius <= 100000 else 0
    except ValueError, TypeError:
        return 0


def parse_feed(payload: dict, kind: str, fetched_at: datetime) -> Feed:
    if not isinstance(payload, dict) or not isinstance(payload.get("outages"), list):
        raise FeedError("Missing outages list")
    generated_at = timestamp(payload.get("generatedAt"))
    if generated_at > fetched_at + timedelta(minutes=10):
        raise FeedError("Feed timestamp is in the future")
    items = []
    skipped_records = 0
    incomplete_addresses = []
    incomplete_coordinates = []
    addressless_records = 0
    try:
        for record in payload["outages"]:
            identifier = text(record["id"])
            if not identifier:
                raise FeedError("Missing outage identifier")
            if kind == "planned":
                ranges = record["addressRanges"]
                intervals = record["intervals"]
                if not isinstance(ranges, list) or not isinstance(intervals, list):
                    raise FeedError("Missing addresses or intervals")
                if not ranges:
                    addressless_records += 1
                addresses = tuple(
                    AddressRange(
                        text(r["city"]),
                        text(r.get("zipCode")),
                        text(r["street"]),
                        text(r.get("startNum")),
                        text(r.get("endNum")),
                    )
                    for r in ranges
                )
                if any(not r.city for r in addresses):
                    raise FeedError("Missing address fields")
                if not intervals:
                    skipped_records += 1
                    incomplete_addresses.extend(addresses)
                    if coordinates := point(record.get("coordinates")):
                        incomplete_coordinates.append(coordinates)
                    continue
                for interval in intervals:
                    start, end = timestamp(interval["from"]), timestamp(interval["to"])
                    cancelled = interval["isCancelled"]
                    if not isinstance(cancelled, bool) or end < start:
                        raise FeedError("Invalid outage interval")
                    if end == start:
                        skipped_records += 1
                        if not cancelled:
                            incomplete_addresses.extend(addresses)
                            if coordinates := point(record.get("coordinates")):
                                incomplete_coordinates.append(coordinates)
                        continue
                    items.append(
                        PlannedOutage(
                            identifier,
                            start,
                            end,
                            addresses,
                            cancelled,
                            text(record.get("status")),
                            point(record.get("coordinates")),
                        )
                    )
            else:
                city = text(record["city"])
                if not city:
                    raise FeedError("Missing fault location")
                consumers = record.get("consumers")
                if consumers is not None and (
                    isinstance(consumers, bool) or not isinstance(consumers, int) or consumers < 0
                ):
                    raise FeedError("Invalid customer count")
                items.append(
                    FaultReport(
                        identifier,
                        city,
                        timestamp(record["from"]),
                        consumers,
                        point(record.get("coordinates")),
                        area_radius(record.get("transformerAreaRadius")),
                    )
                )
    except (KeyError, TypeError, ValueError) as err:
        if isinstance(err, FeedError):
            raise
        raise FeedError("Invalid outage record") from err
    return Feed(
        tuple(items),
        generated_at,
        fetched_at,
        skipped_records=skipped_records,
        incomplete_addresses=tuple(incomplete_addresses),
        incomplete_coordinates=tuple(incomplete_coordinates),
        addressless_records=addressless_records,
    )


def parse_response(raw: bytearray, kind: str, fetched_at: datetime) -> Feed:
    """Decode and normalize a downloaded feed off the event loop."""
    return parse_feed(json.loads(raw), kind, fetched_at)


class EonClient:
    """Shared asynchronous client with a one-minute request cooldown."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self._lock = asyncio.Lock()
        self._snapshot: Snapshot | None = None
        self._last_fetch = float("-inf")

    async def _fetch(self, kind: str, url: str, previous: Feed) -> Feed:
        try:
            async with self.session.get(url, timeout=ClientTimeout(total=30)) as response:
                response.raise_for_status()
                # Cap decompressed response size and ignore untrusted Content-Length.
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise FeedError("Feed too large")
            return await asyncio.to_thread(parse_response, raw, kind, datetime.now(UTC))
        except ClientError, TimeoutError:
            return replace(previous, error="cannot_connect")
        except FeedError, ValueError, UnicodeError:
            return replace(previous, error="invalid_feed")

    async def async_fetch(self) -> Snapshot:
        async with self._lock:
            if self._snapshot and monotonic() - self._last_fetch < MIN_REFRESH_SECONDS:
                return self._snapshot
            previous = self._snapshot or Snapshot(Feed(), Feed())
            planned, faults = await asyncio.gather(
                self._fetch("planned", PLANNED_URL, previous.planned),
                self._fetch("faults", FAULTS_URL, previous.faults),
            )
            self._snapshot = Snapshot(planned, faults)
            self._last_fetch = monotonic()
            return self._snapshot
