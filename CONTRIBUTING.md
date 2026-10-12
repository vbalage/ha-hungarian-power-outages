# Contributing

## Provider adapters

Keep operator-specific networking and parsing under `providers/`. Register a new
adapter in `providers.PROVIDERS`, then add setup labels, translations, documentation
and fixtures. An adapter implements `async_fetch()` returning a normalized
`Snapshot`, with independent `Feed` values for planned outages and faults.

The first release intentionally supports only E.ON. Investigate and test each
additional operator's source before listing it as supported. The existing fault
model supports town/district reports; extend the shared model and matching logic
explicitly if a new operator supplies street or household coverage.

Avoid address submission when bulk public feeds are available. Share requests
across monitored addresses, use timeouts, limit response sizes, and rate-limit
manual refresh. Keep failed feeds distinct from successful empty responses.
Do not infer electricity availability or restoration estimates from absent data.

## Feed changes

Report the integration version, Home Assistant version, which feed failed, and
redacted diagnostics from Settings → Devices & services → Hungarian Power Outages.
Do not post your exact address, coordinates, HA configuration or access tokens.
Provide a small synthetic fixture reproducing the issue where possible.

Test date/time handling in Europe/Budapest, cancellations, house-number parity,
suffixes, partial failures, malformed responses, empty feeds, stale timestamps,
request coalescing and reload/unload behavior. Network calls should be mocked.

## Releases

Run the validation workflow successfully before publishing a GitHub release.
Keep the release tag and `manifest.json` version aligned. Use a beta release for
community testing before submitting for HACS default catalog inclusion.
