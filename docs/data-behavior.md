# Matching and data behavior

## Configure a location

Choose E.ON and enter your town. A town name includes any district/locality suffix
published by E.ON: `Budapest` includes all Budapest districts. To narrow the area,
`Budapest XVI.`, `Budapest 16` and `Budapest 16. kerület` are equivalent. Matching
ignores accents, punctuation and capitalization, and requires whole word
boundaries, so similar town names such as `Érd` and `Erdőkertes` stay separate.
[E.ON's outage map](https://www.eon.hu/hu/lakossagi/aram/aramszunet-informaciok.html)
can help you check the provider's street spelling.

Postcode, street and house number are optional. Leaving them blank intentionally
broadens the matching area. A house number requires a street. Use the operator's
street spelling; accents, capitalization and `u.`/`utca` are normalized, but we
do not guess abbreviated personal names or substitute `út` for `utca`.

Enable **Include nearby reports**, then choose a map point and radius (up to
100 km; the fallback radius is 1 km). Use **Reconfigure** to change the circle or
disable nearby reports. Nearby entities show possible relevance separately from
address matches. Blank source addresses alone do not prove area coverage.

For planned outages, the provider marker must fall inside your circle. For faults,
the integration also checks whether E.ON’s published area circle overlaps yours,
using the same marker centre and radius in metres as its website. Provider map
markers can be approximate; geographic proximity does not confirm that your
house is affected. Records without usable coordinates cannot be matched by radius.

The optional **Parcel number (HRSZ)** field accepts one identifier, such as
`0123/4` or `538/B`, with or without an HRSZ label. Town/district is still required;
street and house number can be left blank. Leading zeros and slash components
are preserved: `0123/4`, `123/4`, `123` and `123/40` are different identifiers.
Exact published parcel identifiers take precedence over street spelling and are
labelled `parcel`. A published different parcel does not count as a match.

If no parcel identifier is published, a supplied street/address can still match.
With only a parcel configured, those records cannot be attributed to your property;
use nearby reports to see geographic candidates. Complex or ambiguous parcel
notations are not expanded into guessed parcel ranges. HRSZ identifies a cadastral
property, not a particular apartment or electrical connection. Fault reports
remain town/radius reports because that feed does not identify parcels.

No geocoding service or account is required. Both feeds are downloaded in full
and your address and search circle are filtered locally; it is not sent to the operator.
The address is stored in Home Assistant's configuration and may be visible in
entity/device names and normal Home Assistant backups.

Use **Reconfigure** to change the address. Entity identifiers stay stable.
Use **Configure** to change polling interval and maximum feed age (default 48
hours). Failed requests or invalid feeds retry every 15 minutes. The daily
interval starts from setup/refresh rather than a fixed time of day.

## What the entities mean

| Entity | Meaning |
| --- | --- |
| Upcoming planned outages | Matching non-cancelled intervals that have not ended; ongoing intervals are included |
| Next planned outage start/end | The earliest matching interval that has not ended; may already be underway |
| Planned outage interval now | At least one matching scheduled interval contains the current time |
| Planned outages calendar | Non-cancelled matching planned intervals within the requested calendar window |
| Reported faults in area | Reports currently present in the town/district feed, including old or zero-customer entries |
| Nearby planned outages / calendar | Planned intervals whose map marker falls within the configured circle |
| Nearby fault reports | Fault markers inside your circle, or provider area circles overlapping it |
| Feed health | `ok`, `degraded`, `stale`, `cannot_connect` or `invalid_feed` |
| Last successful check | When a feed was last successfully downloaded and parsed |
| Feed timestamp | When the operator says the data was generated |
| Refresh outages | Request an update, respecting the shared one-minute cooldown |

Planned summary attributes contain up to 25 matching intervals and their matching
level: `parcel`, `address`, `street`, `area` or `uncertain`. Counts include uncertain matches
so a difficult address format isn't silently reported as all clear. Inspect the
match attribute before using a report to drive an automation.

House-number ranges with two odd or two even endpoints are interpreted as that
side of the street. This follows the feed's separate odd/even range convention;
source addresses should still be checked if precision matters. Individual house
numbers, optional letter suffixes and numbers before a `HRSZ:` annotation are
recognized. Without a configured HRSZ, parcel-only addresses, blank source streets and complex ranges are marked uncertain.

Records without affected addresses are retained for nearby matching when they
have usable coordinates. Feed health is `degraded`, and the global
`source_addressless_records` attribute reports them. Invalid intervals, including
identical start/end times, are skipped and counted in `source_skipped_records`.
If an unusable interval matches your address or circle, the corresponding planned
entities are unavailable. Nearby entities expose
`source_records_without_coordinates`; a zero count cannot establish that every
source record was located or complete.

Nearby report attributes include distance, matching basis and
`household_coverage_confirmed: false`. The nearby calendar is created only when
nearby reports are enabled.
Faults are **area reports, not confirmed household outages**. E.ON sometimes
retains old reports or reports with zero affected customers and incomplete map
coordinates. We retain those details and do not invent a restoration time or
infer precise household coverage from them. Report attributes contain up to 25
entries; `truncated` indicates whether more exist.

Unavailable, malformed or stale feeds make dependent entities unavailable rather
than reporting zero. The independent feed can continue working. Health entities
and previous successful timestamps remain available for troubleshooting. Calendar
queries fail clearly when their feed is unavailable.

The feed snapshot is not persisted across Home Assistant restarts. Setup fetches
again. Planned interval indicators and freshness are recomputed locally each
minute, even with daily polling. This does not discover newly published faults
between polls; use the refresh button when investigating an outage.

