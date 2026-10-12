"""Provider-independent data and conservative Hungarian address matching."""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class MatchLevel(StrEnum):
    PARCEL = "parcel"
    ADDRESS = "address"
    STREET = "street"
    AREA = "area"
    UNCERTAIN = "uncertain"


def normalize(value: str) -> str:
    """Ignore accents, punctuation and case, but retain word boundaries."""
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


BUDAPEST_DISTRICTS = (
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
    "xxi",
    "xxii",
    "xxiii",
)


def normalize_city(value: str) -> str:
    """Accept Budapest district numbers in Roman or Arabic form, with optional kerület."""
    words = normalize(value).split()
    if len(words) >= 2 and words[0] == "budapest":
        if words[-1] in ("kerulet", "ker"):
            words.pop()
        if words[1] in BUDAPEST_DISTRICTS:
            words[1] = str(BUDAPEST_DISTRICTS.index(words[1]) + 1)
        elif words[1].isdigit() and 1 <= int(words[1]) <= 23:
            words[1] = str(int(words[1]))
    return " ".join(words)


def city_matches(query: str, published: str) -> bool:
    """A whole town also includes its published districts/localities, at word boundaries."""
    requested, source = normalize_city(query), normalize_city(published)
    return bool(requested) and (source == requested or source.startswith(requested + " "))


def normalize_street(value: str) -> str:
    words = normalize(value).split()
    if words and words[-1] == "u":
        words[-1] = "utca"
    return " ".join(words)


@dataclass(frozen=True)
class Address:
    city: str
    postcode: str = ""
    street: str = ""
    house_number: str = ""
    parcel_number: str = ""

    @property
    def identity(self) -> str:
        identity = "|".join(
            (
                normalize(self.city),
                self.postcode.strip(),
                normalize_street(self.street),
                normalize(self.house_number),
            )
        )

        # Preserve identities for existing address-only entries.
        if self.parcel_number:
            identity += "|parcel:" + (parcel_number(self.parcel_number) or self.parcel_number)
        return identity


@dataclass(frozen=True)
class AddressRange:
    city: str
    postcode: str
    street: str
    start: str
    end: str = ""


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float

    def distance(self, other: Point) -> float:
        """Great-circle distance in metres, clamping roundoff at antipodes."""
        lat1, lat2 = math.radians(self.latitude), math.radians(other.latitude)
        dlat = lat2 - lat1
        dlon = math.radians(other.longitude - self.longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


@dataclass(frozen=True)
class SearchArea:
    center: Point
    radius: float

    def includes(self, point: Point | None, provider_radius: float = 0) -> bool:
        return point is not None and self.center.distance(point) <= self.radius + provider_radius


@dataclass(frozen=True)
class PlannedOutage:
    id: str
    start: datetime
    end: datetime
    addresses: tuple[AddressRange, ...]
    cancelled: bool = False
    status: str = ""
    coordinates: Point | None = None


@dataclass(frozen=True)
class FaultReport:
    id: str
    city: str
    start: datetime
    consumers: int | None
    coordinates: Point | None = None
    area_radius: float = 0
    # Coordinates and provider circles indicate possible relevance, not household coverage.


@dataclass(frozen=True)
class Feed:
    items: tuple[PlannedOutage | FaultReport, ...] = ()
    generated_at: datetime | None = None
    fetched_at: datetime | None = None
    error: str | None = None
    skipped_records: int = 0
    incomplete_addresses: tuple[AddressRange, ...] = ()
    incomplete_coordinates: tuple[Point, ...] = ()
    addressless_records: int = 0

    def usable(self, now: datetime, stale_hours: int) -> bool:
        return bool(
            not self.error
            and self.generated_at
            and self.fetched_at
            and -timedelta(minutes=10) <= now - self.generated_at <= timedelta(hours=stale_hours)
        )


@dataclass(frozen=True)
class Snapshot:
    planned: Feed
    faults: Feed


# An individual number may have a parcel annotation; do not interpret HRSZ as a house number.
NUMBER = re.compile(r"^([0-9]+)\s*(?:[/ ]?([a-z]))?\.?\s*$", re.I)


def house_number(value: str) -> tuple[int, str] | None:
    before_parcel = re.split(r"\bHRSZ\s*:", value, flags=re.I)[0].strip()
    match = NUMBER.fullmatch(before_parcel)
    return (int(match[1]), (match[2] or "").casefold()) if match else None


PARCEL_LABEL = re.compile(r"\bhrsz\.?\s*:?\s*", re.I)
PARCEL = re.compile(r"[0-9]+(?:/(?:[0-9]+|[a-z]+))*", re.I)


def parcel_number(value: str) -> str | None:
    """Normalize one cadastral identifier; preserve leading zeros and slash boundaries."""
    value = re.sub(r"^\s*hrsz\.?\s*:?\s*", "", value, flags=re.I)
    value = re.sub(r"[.\s]*hrsz\.?\s*$", "", value, flags=re.I)
    value = re.sub(r"\s*/\s*", "/", value.strip().rstrip(".").strip())
    return value.upper() if PARCEL.fullmatch(value) else None


def source_parcels(target: AddressRange) -> tuple[set[str], bool]:
    """Only interpret explicitly labelled source values as parcels, never house numbers."""
    values = set()
    labelled = normalize(target.street) == "hrsz"
    for value in (target.start, target.end):
        label = PARCEL_LABEL.search(value)
        if label:
            labelled = True
            # For a trailing HRSZ label, the preceding identifier is the value.
            candidate = value[label.end() :] or value[: label.start()]
        elif normalize(target.street) == "hrsz":
            candidate = value
        else:
            continue
        if identifier := parcel_number(candidate):
            values.add(identifier)
    return values, labelled


def match_range(address: Address, target: AddressRange) -> MatchLevel | None:
    if not city_matches(address.city, target.city):
        return None
    if address.postcode and target.postcode and address.postcode != target.postcode:
        return None
    if address.parcel_number:
        parcels, labelled = source_parcels(target)
        if parcels:
            return MatchLevel.PARCEL if parcel_number(address.parcel_number) in parcels else None
        # Without published parcel information, use an explicitly supplied street only.
        if not address.street:
            return None
        if labelled:
            return (
                MatchLevel.UNCERTAIN
                if normalize_street(address.street) == normalize_street(target.street)
                else None
            )
    if not address.street:
        return MatchLevel.AREA
    if not target.street:
        return MatchLevel.UNCERTAIN
    if normalize_street(address.street) != normalize_street(target.street):
        return None
    if not address.house_number:
        return MatchLevel.STREET
    number, start, end = (
        house_number(address.house_number),
        house_number(target.start),
        house_number(target.end),
    )
    if number is None or start is None or (target.end and end is None):
        return MatchLevel.UNCERTAIN
    if end is None or start == end:
        if number == start:
            return MatchLevel.ADDRESS
        # A bare house number does not prove whether the suffix is covered.
        if number[0] == start[0] and (not number[1] or not start[1]):
            return MatchLevel.UNCERTAIN
        return None
    if start[0] > end[0]:
        return MatchLevel.UNCERTAIN
    if not start[0] <= number[0] <= end[0]:
        return None
    # E.ON lists separate same-parity endpoint ranges; preserve that distinction.
    if start[0] % 2 == end[0] % 2 and number[0] % 2 != start[0] % 2:
        return None
    if number[1] or start[1] or end[1]:
        return MatchLevel.UNCERTAIN
    return MatchLevel.ADDRESS


def match_outage(address: Address, outage: PlannedOutage) -> MatchLevel | None:
    matches = {match_range(address, item) for item in outage.addresses} - {None}
    for level in (
        MatchLevel.PARCEL,
        MatchLevel.ADDRESS,
        MatchLevel.STREET,
        MatchLevel.AREA,
        MatchLevel.UNCERTAIN,
    ):
        if level in matches:
            return level
    return None
