# Provider requests and UI refresh policy

The UI loads the active calendar/timeline view. Entities are fetched separately
and cached for five minutes. Concurrent identical GETs share one promise.
View retries only happen while `X-MatchNest-Refreshing` is true, after 10, 20,
then 30 seconds (six retries maximum). Hidden tabs stop polling and recheck on
return. Empty completed responses replace stale results. Late responses cannot
overwrite a newly selected range. Account and spoiler variants have separate
local caches. Repeated identical settings payloads do not trigger another save.

Provider refresh jobs use shared keys based on their actual inputs, with UTC
day bounds that expand the requested window. Changing follow categories does
not refetch the same upstream schedule. Historical ranges ending over two days
ago are refreshed at most weekly after a successful fetch. Errors retry after
two minutes, subject to the HTTP cooldown below. The minute notification cron
retains its delivery timing; schedule TTLs govern its provider work.

All eight sports HTTP adapters and provider entity search use `http_cache.py`.
It checks fresh memory/persistent payloads before the network, coalesces requests,
and stores credential-scoped hashed keys. OAuth and notification writes are not
cached. FastF1 retains its existing disk cache and the event-details cache.

| Upstream resource | HTTP success cache |
| --- | --- |
| PandaScore running / upcoming / recent past | 30 seconds / 5 minutes / 2 minutes |
| PandaScore historical past (range ended over two days ago) | 7 days |
| ESPN current month and summaries / future months / historical months | 2 minutes / 6 hours / 7 days |
| football-data fixtures | 6 hours |
| API-Football HTTP payload | 2 minutes; existing fixture/detail caches also apply |
| Jolpica current schedule / results | 6 hours / 2 minutes |
| GRID and TheSportsDB | 6 hours |
| HLTV rankings | 12 hours |
| Team search / other team metadata | 1 hour / 1 day |

The existing provider schedule and event-detail TTLs can be longer than these
HTTP TTLs. HTTP expiry alone does not start a background refresh.

HTTP 429 respects `Retry-After` (seconds or HTTP date). Without it, PandaScore
waits an hour and other providers wait 90 seconds. An exhausted remaining-quota
header also pauses new requests. Authentication/blocked-access failures wait
30 minutes; transient failures wait two minutes. Restricted football-data and
PandaScore resources are paused independently so one inaccessible feed does not
block accessible team feeds. Existing adapter caches remain available as stale
fallbacks on upstream failure.

Conservative local budgets reserve at most 8 football-data requests/minute and
900 PandaScore requests/hour, below their documented free limits of
[10/minute](https://docs.football-data.org/general/v4/policies.html) and
[1,000/hour](https://developers.pandascore.co/docs/rate-and-connections-limits).
Quota reservations and cooldowns persist across restarts. Coalescing and budget
reservation are serialized within one server process. Multiple replicas would
require a distributed lock/atomic quota counter; other apps sharing the same
API key are not included in these local counters.

API-Football is only enabled from environment credentials when
`API_FOOTBALL_ENABLE=1`. An explicit constructor token permits isolated tests.

`GET /health/providers` exposes process-local counts by upstream host:
`network` (attempted requests), `hit` (fresh HTTP cache reuse), and `suppressed`
(cooldown/budget prevented a request). Higher-level cache hits do not reach the
HTTP layer and therefore do not increment these counters. The endpoint is
read-only and exposes neither request URLs nor credentials.

Validation: `python -m unittest discover -s backend/tests` with isolated database
configuration, and `npm test` / `npm run build` in `web`.
