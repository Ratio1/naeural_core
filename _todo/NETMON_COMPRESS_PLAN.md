# NET_MON Compression Plan

## Direct Answer
- Compress on the core sender in `naeural_core/business/default/admin/net_mon_01.py` inside `_process`, after `_create_payload(...)`, but at the same filtered wire-field boundary that `__process_payload()` / `payload.to_dict()` would produce.
- Decompress on the core receiver in `naeural_core/business/default/admin/net_config_monitor.py` at the start of `netmon_handler`, before `CURRENT_NETWORK` is read and before whitelist conversion.
- Add a shared SDK/helper decode function in `ratio1/const/payload.py`, and call it from `ratio1/base/generic_session.py` inside `__maybe_process_net_mon`, before the first `CURRENT_NETWORK` access.
- Use heartbeat-style `NETMON_VERSION="v2"` plus `ENCODED_DATA` for the non-`EE_*` NET_MON body. Do not replace `CURRENT_NETWORK` in place with a compressed string.
- Gate sender compression with `EE_NETMON_COMPRESS`. If unset, default to `ON` when `EE_ETH_ENABLED=true` and `OFF` when `EE_ETH_ENABLED=false`. Receiver decode must always accept both formats.

## Goal
Reduce `NET_MON_01` bandwidth with the smallest safe change set while preserving mixed-fleet compatibility and existing NET_MON semantics.

## Decision
Phase 1 should compress the NET_MON business body in heartbeat style, not compress `CURRENT_NETWORK` in place.

Why:
- the measured savings difference between `CURRENT_NETWORK`-only compression and full NET_MON-body compression is small
- in-place type changes are fragile because existing readers expect `CURRENT_NETWORK` to stay a dict
- the hb-style wrapper keeps the change localized to one sender path and two receiver paths

## Wire Shape

### Existing `v1`
- current top-level NET_MON payload remains unchanged
- `CURRENT_NETWORK`, `CURRENT_ALERTED`, `CURRENT_RANKING`, `WHITELIST_MAP`, `MESSAGE`, `STATUS`, and the other non-`EE_*` fields remain top-level

### Proposed `v2`
- keep existing `EE_*` fields top-level
- keep transport/meta routing keys top-level:
  - `STREAM_NAME`
  - `SIGNATURE`
  - `INSTANCE_ID`
  - `SESSION_ID`
  - `INITIATOR_ID`
  - `INITIATOR_ADDR`
  - `MODIFIED_BY_ID`
  - `MODIFIED_BY_ADDR`
  - `USE_LOCAL_COMMS_ONLY`
- add `NETMON_VERSION="v2"`
- add `ENCODED_DATA=<zlib+base64 compressed JSON>`
- move NET_MON business fields such as `CURRENT_NETWORK`, `CURRENT_ALERTED`, `CURRENT_RANKING`, `WHITELIST_MAP`, `MESSAGE`, `STATUS`, `CURRENT_NEW`, and similar non-routing fields into the encoded body
- keep uncompressed behavior as implicit `v1`

## Minimal Core Changes

### 1. Sender toggle and default
- File: `naeural_core/constants.py`
- Add env key constant:
  - `EE_NETMON_COMPRESS_ENV_KEY = "EE_NETMON_COMPRESS"`
- Defaulting rule:
  - if `EE_NETMON_COMPRESS` is explicitly set, honor it
  - otherwise default to `ct.ETH_ENABLED`
  - `EE_ETH_ENABLED=true` -> default compression `ON`
  - `EE_ETH_ENABLED=false` -> default compression `OFF`

This intentionally stays env-driven in phase 1 because:
- the request explicitly asked for an env var
- `NET_MON_01` already uses env-driven behavior
- the change stays plugin-scoped instead of adding another orchestrator-level startup-config branch

### 2. Core sender compression point
- File: `naeural_core/business/default/admin/net_mon_01.py`
- Method: `_process`
- Touchpoint: immediately after `payload = self._create_payload(...)` and before `return payload`

Planned behavior:
1. Build the normal NET_MON payload object exactly as today.
2. If compression is enabled:
   - operate on the same filtered wire field set that `__process_payload()` / `payload.to_dict()` would emit
   - do not operate on `vars(payload)` / raw `__dict__`
   - keep `EE_*` fields top-level
   - keep transport/meta routing keys top-level so the comm layer can still build `EE_PAYLOAD_PATH` and routing metadata
   - move NET_MON business fields into the encoded body
   - add `NETMON_VERSION="v2"` and `ENCODED_DATA=<compressed body>`
   - do not also ship raw `CURRENT_NETWORK`
3. If compression is disabled:
   - keep the current wire shape unchanged

Implementation constraint:
- avoid introducing an extra ad hoc object-serialization path that double-processes payload side effects
- the compression boundary must match the actual wire dict boundary, not raw Python object internals

### 3. Core receiver expansion point
- File: `naeural_core/business/default/admin/net_config_monitor.py`
- Method: `netmon_handler`
- Touchpoint: before:
  - `current_network = data.get(...)`
  - `maybe_convert_netmon_whitelist(data)`

Planned behavior:
1. Expand `v2` NET_MON payloads back into the normal dict shape.
2. Run the existing whitelist conversion and peer-status logic unchanged.

## Minimal SDK Changes

### 4. Shared decode helper
- File: `ratio1/const/payload.py`
- Add a tolerant helper near `maybe_convert_netmon_whitelist(...)`

Required behavior:
- if `NETMON_VERSION != "v2"`, return unchanged
- if `ENCODED_DATA` is missing, return unchanged
- decode with the same codec as heartbeat: `log.decompress_text(...)` on zlib+base64 text
- `json.loads(...)` the decoded body and merge it into the payload dict
- be idempotent:
  - if `CURRENT_NETWORK` is already a dict, do nothing
  - do not break if the helper is called more than once
- fail closed:
  - if decode or JSON parse fails, leave the payload unchanged and let existing NET_MON handling reject it naturally instead of crashing

Required constant:
- `NETMON_VERSION = "NETMON_VERSION"`

### 5. SDK decode insertion point
- File: `ratio1/base/generic_session.py`
- Method: `__maybe_process_net_mon`
- Touchpoint: before the first `CURRENT_NETWORK` access

Planned behavior:
1. Inside the existing NET_MON-specific branch, call the shared NET_MON decode helper.
2. Let the helper mutate `dict_msg` in place so later SDK callbacks and transaction handling see the normalized dict without widening generic payload handling.

Why here:
- it is already the first SDK path that consumes `CURRENT_NETWORK`
- `__on_payload()` calls `__maybe_process_net_mon(...)` before user callbacks, so in-place mutation here is enough for downstream SDK consumers too
- this keeps the change NET_MON-scoped instead of modifying generic payload handling

## Rollout Order
1. Ship receiver decode support first:
   - core `net_config_monitor`
   - SDK `__maybe_process_net_mon`
   - shared helper in `ratio1/const/payload.py`
2. Keep sender compression disabled initially.
3. Validate that updated readers accept both:
   - existing uncompressed `v1`
   - compressed `v2`
4. Enable sender compression with `EE_NETMON_COMPRESS=true` in controlled EVM environments.
5. Only after mixed-fleet decode support is confirmed, rely on the default:
   - `EE_ETH_ENABLED=true` -> compression on by default
   - `EE_ETH_ENABLED=false` -> compression off by default

## Implementation Tracking

Status legend:
- `[ ]` not started
- `[~]` in progress
- `[x]` done

Tracking rule:
- when a phase is completed, mark the completed items in that phase as `[x]`
- add one completion note in `Phase Completion Log`
- keep the completion note factual: date, files changed, tests run, result, follow-up risk if any

## Per-Phase Implementation Plan

### Phase 0. Lock the wire contract and test fixtures

Implementation plan:
1. [x] Add a small set of canonical NET_MON fixtures:
   - uncompressed `v1`
   - compressed `v2`
   - malformed `v2`
2. [x] Define the exact field split for `v2`:
   - top-level `EE_*`
   - top-level routing/meta keys required before comm-layer packaging:
     - `STREAM_NAME`
     - `SIGNATURE`
     - `INSTANCE_ID`
     - `SESSION_ID`
     - `INITIATOR_ID`
     - `INITIATOR_ADDR`
     - `MODIFIED_BY_ID`
     - `MODIFIED_BY_ADDR`
     - `USE_LOCAL_COMMS_ONLY`
   - top-level `NETMON_VERSION`
   - top-level `ENCODED_DATA`
   - decoded body contains all non-`EE_*` NET_MON business fields
3. [x] Reuse the same compact JSON and zlib+base64 codec assumptions already used by heartbeat.
4. [x] Document one explicit normalization rule:
   - after decode, downstream code must observe the same dict shape it sees today for `v1`

Test plan:
- build fixture payloads from real captured NET_MON samples when possible
- confirm fixture `v2` body expands to the same business dict as fixture `v1`
- confirm malformed `v2` remains non-fatal and does not silently invent `CURRENT_NETWORK`

Acceptance criteria:
- there is one agreed example of `v1` and `v2` for implementation and tests
- the team can point to one exact definition of which fields stay top-level versus encoded
- malformed fixture behavior is explicitly defined as non-crashing and fail-closed

### Phase 1. Add tolerant reader-side decode support

Implementation plan:
1. [x] In `ratio1/const/payload.py`, add a NET_MON decode helper beside `maybe_convert_netmon_whitelist(...)`.
2. [x] Make the helper:
   - return unchanged for non-`v2`
   - return unchanged when `ENCODED_DATA` is absent
   - decode and merge only when needed
   - do nothing if `CURRENT_NETWORK` is already a dict
3. [x] In `ratio1/base/generic_session.py`, call the helper inside `__maybe_process_net_mon` before the first `CURRENT_NETWORK` access.
4. [x] In `naeural_core/business/default/admin/net_config_monitor.py`, call the same decode path or equivalent core-side expansion before reading `CURRENT_NETWORK` and before whitelist conversion.
5. [x] Keep all sender behavior unchanged in this phase.

Test plan:
- unit-test helper behavior for:
  - `v1` passthrough
  - valid `v2` decode
  - repeated decode call on the same dict
  - malformed `ENCODED_DATA`
  - malformed decoded JSON
- unit-test core `netmon_handler` with:
  - current `v1`
  - valid `v2`
  - `v2` plus whitelist map conversion
- unit-test SDK `__maybe_process_net_mon` path with:
  - valid `v2` expands before `CURRENT_NETWORK` processing
  - downstream callback-visible dict is normalized

Acceptance criteria:
- updated core readers accept both `v1` and `v2`
- updated SDK readers accept both `v1` and `v2`
- malformed `v2` does not crash core or SDK processing
- whitelist conversion still runs only on normalized dict data

### Phase 2. Add sender compression behind an explicit flag

Implementation plan:
1. [x] Add `EE_NETMON_COMPRESS_ENV_KEY = "EE_NETMON_COMPRESS"` in `naeural_core/constants.py`.
2. [x] In `net_mon_01.py`, compute the effective compression toggle:
   - explicit `EE_NETMON_COMPRESS` wins
   - otherwise default to `ct.ETH_ENABLED`
3. [x] Add the sender transformation immediately after normal NET_MON payload creation and at the wire-dict boundary.
4. [x] Preserve the current `v1` path unchanged when compression is disabled.
5. [x] Ensure `v2` sender output does not include raw `CURRENT_NETWORK` or duplicate business keys outside `ENCODED_DATA`.

Test plan:
- unit-test sender toggle behavior for:
  - explicit `true`
  - explicit `false`
  - unset with `EE_ETH_ENABLED=true`
  - unset with `EE_ETH_ENABLED=false`
- unit-test sender payload shape for:
  - uncompressed `v1` output unchanged
  - compressed `v2` output contains top-level `EE_*`, `NETMON_VERSION`, `ENCODED_DATA`
  - compressed `v2` output excludes raw `CURRENT_NETWORK`
- regression-test that the compressed body round-trips through the Phase 1 reader path back to the current dict shape

Acceptance criteria:
- sender emits byte-for-byte compatible `v1` shape when compression is off
- sender emits valid `v2` shape when compression is on
- explicit `EE_NETMON_COMPRESS` overrides the ETH-derived default in both directions
- `v2` round-trip through decode yields the same business dict semantics as current `v1`

### Phase 3. Mixed-fleet rollout and passive validation

Implementation plan:
1. [ ] Deploy Phase 1 reader support everywhere that consumes NET_MON:
   - core receivers
   - SDK listeners
2. [ ] Leave sender compression disabled by default until reader coverage is confirmed.
3. [ ] Enable `EE_NETMON_COMPRESS=true` only in a controlled EVM subset.
4. [ ] Observe bandwidth and semantic parity before broadening rollout.
5. [ ] After mixed-fleet confidence is established, allow the env-default behavior to carry the rollout.

Test plan:
- passive listener capture before and after enablement:
  - raw MQTT payload bytes
  - message counts
  - decode success/failure counts if instrumented
- compare derived peer state before and after enablement:
  - online/offline visibility
  - allow-list / whitelist outcomes
  - callback-visible `CURRENT_NETWORK` shape
- run at least one rollback drill:
  - set `EE_NETMON_COMPRESS=false`
  - confirm sender returns to `v1`

Execution checklist:
1. Preflight reader coverage:
   - confirm deployed core receivers include the `net_config_monitor` decode change
   - confirm deployed SDK listeners include the `__maybe_process_net_mon` decode change
   - confirm sender compression remains disabled by default on the target fleet before the canary
2. Baseline passive raw-bandwidth capture:
   - from the `naeural_core` repo root run:
     - `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 600 --max-messages 30000`
   - record:
     - capture jsonl path
     - summary json path
     - results md path
     - `NET_MON_01` raw bytes from the summary
     - total raw bytes from the summary
3. Baseline NET_MON-specific probe:
   - from the `naeural_core` repo root run:
     - `python3 xperimental/payloads_tests/netmon_compression_probe.py --seconds 180 --target-count 30`
   - record:
     - sample count
     - average raw NET_MON size
     - estimated hb-style reduction
     - results md path
4. Canary enablement:
   - enable `EE_NETMON_COMPRESS=true` only on the selected sender subset
   - keep all non-canary senders on the existing behavior
   - do not widen the rollout until the post-enable capture is reviewed
5. Post-enable passive raw-bandwidth capture:
   - rerun:
     - `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 600 --max-messages 30000`
   - compare baseline vs canary:
     - `NET_MON_01` raw bytes
     - total raw bytes
     - by-sender NET_MON share if visible in the summary
6. Post-enable semantic check:
   - compare before/after peer and allow-list state in the updated SDK/core consumers
   - confirm callbacks still see normalized `CURRENT_NETWORK` dicts
   - confirm no malformed-payload crashes or repeated decode failures are observed in logs
7. Rollback drill:
   - revert the canary senders to `EE_NETMON_COMPRESS=false`
   - rerun:
     - `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 300 --max-messages 15000`
   - confirm sender behavior returns to `v1`-like wire shape and semantic parity remains intact
8. Promotion gate:
   - only widen rollout if the canary shows lower raw NET_MON bytes and no peer/whitelist regression
   - otherwise keep compression disabled and log the blocking evidence in `Phase Completion Log`

Evidence to record in the completion note:
- baseline and post-enable results md paths
- baseline and post-enable summary json paths
- sender subset used for the canary
- observed `NET_MON_01` raw-byte delta
- observed total raw-byte delta
- semantic parity result for peer discovery and whitelist handling
- rollback result

Current local status:
- in progress; baseline capture evidence now exists locally
- completed evidence:
  - [x] baseline passive raw-bandwidth capture
    - valid artifact set:
      - `xperimental/payloads_tests/evidence/raw_bandwidth/20260319T195320+0000_mainnet_bandwidth.jsonl`
      - `xperimental/payloads_tests/evidence/raw_bandwidth/20260319T195320+0000_mainnet_bandwidth_summary.json`
      - `xperimental/payloads_tests/evidence/raw_bandwidth/20260319T195320+0000_mainnet_bandwidth_results.md`
    - validated capture window: `2026-03-19T19:53:20+00:00` to `2026-03-19T20:03:20+00:00` (`600.098s`)
    - key totals:
      - total raw bytes: `3521157` (`5.7KB/s`, `343.8KB/min`)
      - `payload:NET_MON_01` raw bytes: `841780` across `32` messages (`23.9%` of observed bytes)
  - [x] baseline NET_MON-specific probe
    - valid artifact:
      - `xperimental/payloads_tests/evidence/netmon_compression/20260319T175839+0000_netmon_compression_results.md`
    - key totals:
      - sample count: `8`
      - average raw NET_MON size: `25.9KB`
      - estimated hb-style reduction: `62.7%`
- superseded local artifact:
  - `xperimental/payloads_tests/evidence/raw_bandwidth/20260319T175836+0000_mainnet_bandwidth_results.md`
  - do not use it as Phase 3 baseline evidence because the reported window was inconsistent (`--seconds 600` run but results rendered `6510.1s`)
- next requirement:
  - confirm deployed reader coverage, then execute canary enablement and the post-enable capture steps above

Acceptance criteria:
- readers already in production continue to function with sender compression disabled
- controlled `v2` rollout shows smaller raw NET_MON payloads
- no observed regression in peer discovery, whitelist handling, or callback-visible state
- rollback to `v1` is a simple config change and restores prior sender behavior

### Phase 4. Default-on cleanup and hardening

Implementation plan:
1. [ ] Make the ETH-based default operational only after rollout evidence is satisfactory.
2. [ ] Add or update any operator notes and runbooks that mention NET_MON payload expectations.
3. [ ] Keep receiver dual-format support in place for mixed-version tolerance.
4. [ ] Add targeted logging or counters only if needed to detect decode failures without creating noise.

Test plan:
- verify default behavior in both startup modes:
  - `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=true`
  - `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=false`
- confirm explicit override still defeats the default after rollout
- confirm operational docs reflect the final default and rollback path

Execution checklist:
1. Default-on readiness review:
   - confirm Phase 3 canary evidence shows lower raw NET_MON bytes with no semantic regression
   - confirm rollback was exercised successfully during Phase 3
2. Default behavior verification:
   - on an ETH-enabled deployment with `EE_NETMON_COMPRESS` unset, confirm outgoing NET_MON payloads use `NETMON_VERSION="v2"`
   - on a non-ETH deployment with `EE_NETMON_COMPRESS` unset, confirm outgoing NET_MON payloads remain uncompressed `v1`
3. Explicit override verification:
   - set `EE_NETMON_COMPRESS=false` on an ETH-enabled deployment and confirm sender reverts to `v1`
   - set `EE_NETMON_COMPRESS=true` on a non-ETH deployment and confirm sender emits `v2`
4. Operator documentation review:
   - document the final default rule
   - document the explicit override behavior
   - document the rollback path
5. Post-default passive confirmation:
   - rerun:
     - `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 600 --max-messages 30000`
   - confirm the expected raw-bandwidth improvement persists after broader enablement

Evidence to record in the completion note:
- default-on verification environment(s)
- evidence that unset behavior matches the ETH-driven rule
- evidence that explicit override still works in both directions
- updated doc/runbook path if one was changed
- post-default results md path and summary json path

Acceptance criteria:
- default behavior matches the documented ETH-driven rule
- explicit env override still works after default-on rollout
- operators have a documented enable/disable path and mixed-fleet compatibility note
- dual-format receiver support remains intact

## Phase Completion Log

Use one entry per completed phase.

### Phase 0 completion
- Date: `2026-03-19`
- Status: done
- Summary: Added canonical `v1` / `v2` / malformed fixture builders in targeted tests and locked the exact safe top-level field split for transport/meta versus NET_MON business data.
- Files changed:
  - `ratio1_sdk/tests/test_netmon_payload.py`
  - `naeural_core/naeural_core/business/test_framework/test_netmon_compression.py`
  - `naeural_core/_todo/NETMON_COMPRESS_PLAN.md`
- Tests run:
  - `python3 -m unittest discover -s /home/vi/work/ratio1/repos/edge_node/ratio1_sdk/tests -p 'test_netmon_payload.py'`
  - `MPLCONFIGDIR=/tmp/mpl python3 -m unittest naeural_core.business.test_framework.test_netmon_compression`
- Acceptance result: pass locally; the transport-safe `v2` envelope contract is now encoded in tests and documented in this plan.
- Open follow-up: verify the same contract under a full live NET_MON emission path during Phase 3 rollout.

### Phase 1 completion
- Date: `2026-03-19`
- Status: done
- Summary: Added tolerant `v2` decode support in the shared payload helper, the SDK NET_MON path, and the core NET_MON receiver path.
- Files changed:
  - `ratio1_sdk/ratio1/const/payload.py`
  - `ratio1_sdk/ratio1/base/generic_session.py`
  - `naeural_core/naeural_core/business/default/admin/net_config_monitor.py`
  - `ratio1_sdk/tests/test_netmon_payload.py`
  - `naeural_core/naeural_core/business/test_framework/test_netmon_compression.py`
- Tests run:
  - `python3 -m unittest discover -s /home/vi/work/ratio1/repos/edge_node/ratio1_sdk/tests -p 'test_netmon_payload.py'`
  - `MPLCONFIGDIR=/tmp/mpl python3 -m unittest naeural_core.business.test_framework.test_netmon_compression`
- Acceptance result: pass locally; `v1` passthrough, `v2` decode, idempotent decode, malformed decode failure, and whitelist conversion-after-decode are covered.
- Open follow-up: exercise the core receiver path with a fuller runtime harness once a safe plugin import/startup harness is available.

### Phase 2 completion
- Date: `2026-03-19`
- Status: done
- Summary: Added `EE_NETMON_COMPRESS` configuration, defaulted it from `EE_ETH_ENABLED` when unset, and wrapped NET_MON payload serialization so sender compression happens at the payload `to_dict()` boundary without changing the normal `v1` path when disabled.
- Files changed:
  - `naeural_core/naeural_core/constants.py`
  - `naeural_core/naeural_core/business/default/admin/net_mon_01.py`
  - `ratio1_sdk/ratio1/const/payload.py`
  - `ratio1_sdk/tests/test_netmon_payload.py`
  - `naeural_core/naeural_core/business/test_framework/test_netmon_compression.py`
- Tests run:
  - `PYTHONPYCACHEPREFIX=/tmp/codex-pyc python3 -m compileall /home/vi/work/ratio1/repos/edge_node/naeural_core/naeural_core/constants.py /home/vi/work/ratio1/repos/edge_node/naeural_core/naeural_core/business/default/admin/net_mon_01.py`
  - `python3 -m unittest discover -s /home/vi/work/ratio1/repos/edge_node/ratio1_sdk/tests -p 'test_netmon_payload.py'`
  - `MPLCONFIGDIR=/tmp/mpl python3 -m unittest naeural_core.business.test_framework.test_netmon_compression`
- Acceptance result: pass locally; sender-side envelope generation is implemented and verified through helper/wire-shape tests and compile checks.
- Open follow-up: confirm end-to-end sender toggle behavior through a live NET_MON plugin execution path before enabling Phase 3 rollout.

Template:

### Phase N completion
- Date:
- Status: done
- Summary:
- Files changed:
- Tests run:
- Acceptance result:
- Open follow-up:

## Corner Cases

### Sender boundary
- do not compress raw `GeneralPayload.__dict__`
- do not accidentally include internal members such as `owner`
- do not introduce a second payload-processing path that diverges from normal wire serialization

### Idempotent decode
- the decode helper must tolerate repeated calls in the same process
- `v2` payloads that were already expanded must not be re-decoded or re-merged

### Decode failure
- malformed `ENCODED_DATA` must not crash SDK or core receivers
- the fallback behavior should be equivalent to “compressed NET_MON payload not understood”

### Whitelist order
- decode first
- run `maybe_convert_netmon_whitelist(...)` only after `CURRENT_NETWORK` and `WHITELIST_MAP` are back in normal dict form

### Mixed fleet
- old readers will not understand compressed NET_MON payloads
- sender compression must not become the default operational behavior until decode support is deployed on both core and SDK sides

### Callback visibility
- SDK callbacks should see the normalized NET_MON dict after decode
- keep this scoped to NET_MON payloads only; do not add generic payload decompression logic

### Explicit override
- `EE_NETMON_COMPRESS=false` must override the ETH-enabled default
- `EE_NETMON_COMPRESS=true` must allow opt-in compression even when `EE_ETH_ENABLED=false`

## Verification Plan

### Core
- `v1` NET_MON payload still works unchanged through `net_config_monitor.netmon_handler`
- `v2` NET_MON payload expands before whitelist conversion and preserves the same `CURRENT_NETWORK` semantics
- EVM whitelist-map conversion still works after expansion
- `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=true` -> sender uses compressed path
- `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=false` -> sender uses uncompressed path
- explicit `EE_NETMON_COMPRESS=false` overrides the ETH default

### SDK
- `v2` NET_MON payload injected into `GenericSession.__maybe_process_net_mon` is expanded before NET_MON processing
- pipeline callbacks and `custom_on_payload(...)` see the normalized dict after the NET_MON branch mutates it in place
- heartbeat decode remains unchanged

### Live smoke
- passive listener sees smaller raw NET_MON payloads when compression is enabled
- updated SDK still derives the same peer/allow-list state as before

## Non-Goals
- no topic changes
- no heartbeat redesign
- no generic payload compression framework
- no NET_MON delta protocol in this change
- no UI/API redesign
