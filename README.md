# Hungarian Power Outages

Home Assistant integration for **E.ON Hungary** planned outages and published fault reports.
Match your town, address or parcel number (HRSZ), check nearby reports by radius, and refresh on demand.
Requires **Home Assistant 2026.9.4+**. No E.ON account, API key or YAML is needed.

**Companion:** [Hungarian Power Outages Card](https://github.com/vbalage/hungarian-power-outages-card)
provides an overview, structured report details and a combined outage map. Install this integration first;
the card is optional, and standard Home Assistant cards also work.

![Companion card with demonstration data](https://raw.githubusercontent.com/vbalage/hungarian-power-outages-card/main/docs/screenshots/overview.png)

## Install

**HACS custom repository** (not yet in the default HACS catalog):

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/vbalage/ha-hungarian-power-outages`, category **Integration**.
3. Download **Hungarian Power Outages**, then restart Home Assistant.

**Manual:** download this repository's ZIP from **Code → Download ZIP**. Copy only
`custom_components/hungarian_power_outages` into `/config/custom_components/`, then restart.
For Home Assistant Container, `/config` is your mounted configuration directory.

## Set up a location

Open **Settings → Devices & services → Add integration → Hungarian Power Outages**.
Choose E.ON and configure:

| Setting | What to enter |
| --- | --- |
| Town/district | Required. `Budapest` includes all districts; `Budapest XVI.` or `Budapest 16` narrows the area. Accents and capitalization are ignored. |
| Postcode, street, house number | Optional filters. A house number needs a street. Leave blank to match a wider area; omit floors and doors. |
| Parcel number (HRSZ) | Optional **single** identifier, e.g. `0123/4`. Town is still required; street/house number may be blank. Leading zeros and slash components must match exactly. |
| Nearby reports | Enable, choose a map point and radius. These reports are geographic candidates, separate from address/parcel matches. |

Use your device entry's **Reconfigure** action to change the location, parcel or radius.
Use **Configure** to change polling (default **24 hours**, range 1–168 hours) and maximum feed age
(default 48 hours). Add another integration entry for another location.

## Use

- **Upcoming planned outages**, start/end sensors and the calendar show matching scheduled intervals.
- **Planned outage interval now** indicates that a matching scheduled interval is happening now.
- Fault sensors show reports for the town/district; nearby sensors and calendar cover your search circle.
- Press **Refresh outages** to check manually, or call `button.press` on that entity from an automation.
  All entries share downloads and a **60-second cooldown**. Failed downloads retry after 15 minutes.
- Add the [companion card](https://github.com/vbalage/hungarian-power-outages-card#install) for the
  refresh button, report lists, feed status and map. Both projects have Hungarian and English labels.

## Coverage and troubleshooting

This is an unofficial community integration using E.ON's public website feeds.
Reports **do not measure whether your house has electricity** or guarantee restoration times.
Faults and radius matches do not confirm household coverage; missing reports do not mean power is OK.

Parcel matching needs the **exact published HRSZ**. If a record has no parcel, a supplied street/address
can still match; a parcel-only setup cannot identify that record as your property. Nearby matching may help.
Provider coordinates are approximate, not parcel boundaries. Lists/map attributes show up to **25 reports per sensor**.

Incomplete provider records are explained as a coverage note in the card. Stale, invalid or failed feeds
make dependent entities unavailable rather than reporting zero. An unusable interval relevant to your
location can also make planned entities unavailable. See [matching and data behavior](docs/data-behavior.md).

If setup is missing, restart Home Assistant and refresh the browser. For feed issues, use the refresh
button, inspect the feed-health entities and Home Assistant logs, or [report an issue](https://github.com/vbalage/ha-hungarian-power-outages/issues)
with redacted integration diagnostics. Addresses and radius filtering happen locally; normal HA backups
can contain your configured location. Avoid posting personal configuration or tokens.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests, provider adapters and feed changes.
Only E.ON is currently supported; other providers can be added through adapters. MIT licensed.

## Support

If you find this project useful, you can [buy me a coffee](https://buymeacoffee.com/balazsviszy).
Support is optional; the project remains free and open source.
