# Backend

FastAPI service managed with [uv](https://docs.astral.sh/uv/).

## Development

```bash
uv sync
uv run backend
```

The API is available at `http://localhost:8000`; interactive documentation is
at `http://localhost:8000/docs`.

## Tests

```bash
uv run pytest
```

## Breakout and event controls

The AI analysis payload includes computed `breakout_levels` (`closed_confirmed`,
`intrabar_broken`, `touched`). H1/D1/H4 levels need the latest closed H1 body to
cross the level by at least `0.1 × ATR_H1`, or two consecutive closed M15 bars to
close beyond it by at least `0.1 × ATR_M15` each. M15/M30 levels use the M15 rule.
Unconfirmed breakout entries are held; genuine pullback limits remain eligible.

The packaged [calendar](src/backend/trading_core/riskgate/economic_calendar.json)
covers late November and December 2026. Update it against [Fed](https://www.federalreserve.gov/newsevents/2026-december.htm),
[BLS](https://www.bls.gov/schedule/2026/12_sched_list.htm), and [BEA](https://www.bea.gov/news/schedule/)
release schedules. Set `GB_ECONOMIC_CALENDAR_PATH` to an absolute path to override
it (`/code/data/economic_calendar.json` is an option in Docker Compose). Each entry
has a `type`, a UTC `time_utc`, and a list of canonical `symbols`. Restart after
editing. The server does not fetch calendar updates; unlisted dates are unguarded.

| Window (UTC) | Controls for affected symbols |
| --- | --- |
| `T−24h` to `T−4h` | Warn only |
| `T−4h` to `T−30m` | Half size, no add-ons or double pending orders, expire by `T−30m` |
| `T−30m` to `T+30m` (`T+60m` for FOMC decisions) | No new orders; batch-cancel EA-owned pending orders. Do not move existing SL/TP or close positions because of the event. |

Compile and update both MT4 and MT5 EAs before using event controls live. Old EAs
cannot receive reduced-size orders or execute batch cancellation. A live
(`cutover`) account needs a connected EA to receive and acknowledge the batch;
check the result on a demo account, including broker-side expiration support and
rejection below the broker's minimum volume. A failed cancellation does not
guarantee that a pending order was removed.
