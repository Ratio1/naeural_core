# Working-hours evaluation

The shared `_WorkingHoursMixin` controls schedule gating for ordinary
`BasePluginExecutor` descendants, including `CVPluginExecutor`. Plugins continue
to import their existing bases; no application-specific adapter is required.

Runtime evaluation converts the current UTC instant into `WORKING_HOURS_TIMEZONE`
(or the logger timezone when the setting is None) and selects that zone's weekday.
It does not convert recurring wall times through an arbitrary historical date or
read a naive host-local clock. The parser retains the logger's IANA/POSIX strings,
fixed offsets such as `UTC+02:30`, integer hour offsets and tzinfo objects. Invalid
zones on nonempty, non-ignored schedules produce a validation error rather than
silently selecting host time. Unrestricted and ignored schedules do not require
a resolvable host timezone, including during configuration validation.

Intervals include both entire endpoint minutes: `06:00–07:00` remains active
through `07:00:59`, then becomes inactive at `07:01:00`. Equal endpoints select
one full minute. Daily overnight intervals wrap midnight; weekly overnight
intervals belong to their starting weekday and carry into the following day.
For a cold start in that carry, callbacks receive the starting weekday and the
original interval index. An active shift is not restarted at midnight or when
one overlapping interval replaces another; existing start/end transition state,
hooks, notifications and payload fields remain in place.

Global `[]` is unrestricted. None, `{}`, missing weekdays and empty weekday lists
are inactive unless a previous day's overnight interval is still active.
`IGNORE_WORKING_HOURS` bypasses schedule evaluation. The existing execution loop
checks forced pause before working hours; this correction does not change that
precedence or the general logger's half-open interval helper.

The public `working_hours` property and explicit conversion helpers retain their
legacy edge-local representation for compatibility. That representation cannot
encode current seasonal offsets or repeated DST hours and must not be used for
new execution decisions. Internal evaluation and shift metadata use the
normalized source schedule instead.

## Verification and release boundary

Run from the repository root:

```sh
python3 -m unittest discover -s naeural_core/business/test_framework -p test_working_hours.py
python3 -m unittest discover -s naeural_core/business/test_framework -p 'test_*.py'
python3 -m compileall naeural_core/business/mixins_base/working_hours_mixin.py naeural_core/business/test_framework/test_working_hours.py
```

This changes scheduled execution for all shared-base consumers, including non-CV
plugins. Verify the consuming application against the published core package and
its exact built image, with boundary/DST cases and shift events. A local source
checkout or a mounted-source container test is not a published runtime update.
Consumers that previously used a half-open end must account for the inclusive
last minute; select and roll out one schedule contract across API and runtime.
Equal endpoints previously selected no time and now select one full minute.
A weekly `SUN 22:00–02:00` previously wrapped within Sunday's lookup; it now
starts Sunday at 22:00 and ends Monday after 02:00:59. Include both changes in
the upstream package release notes and downstream schedule acceptance.
