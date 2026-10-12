"""Diagnostics never include the configured address, identifiers or coordinates."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    result = {
        "provider": entry.data["provider"],
        "poll_hours": coordinator.poll_hours,
        "stale_hours": coordinator.stale_hours,
        "feeds": {},
    }
    for kind in ("planned", "faults"):
        feed = coordinator.feed(kind)
        result["feeds"][kind] = {
            "error": feed.error,
            "record_count": len(feed.items),
            "skipped_records": feed.skipped_records,
            "addressless_records": feed.addressless_records,
            "generated_at": feed.generated_at.isoformat() if feed.generated_at else None,
            "last_success": feed.fetched_at.isoformat() if feed.fetched_at else None,
            "usable": coordinator.usable(kind),
        }
    return result
