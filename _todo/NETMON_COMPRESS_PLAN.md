# NET_MON Compression Plan

## Goal
Reduce `NET_MON_01` bandwidth with the smallest safe change set, following the heartbeat compression model closely enough that core and SDK behavior stay predictable in mixed fleets.

## Recommendation
Do not replace `CURRENT_NETWORK` in-place with a compressed string. The safer minimal plan is to compress the NET_MON business body, whose dominant field is `CURRENT_NETWORK`, into `ENCODED_DATA` behind a `NETMON_VERSION="v2"` marker.

This keeps the outer payload envelope unchanged, mirrors the heartbeat `v2` pattern, and avoids breaking readers that currently assume `CURRENT_NETWORK` is a dict.

## Why This Shape
- The measured savings difference is small:
  - `CURRENT_NETWORK`-only compression saved `80.2%` of sampled `NET_MON_01` payload bytes.
  - hb-style compression of the full non-`EE_*` NET_MON body saved `81.8%`.
  - On the measured 10-minute raw sample, the difference was only about `1.0MB` (`49.4MB` vs `50.4MB` saved).
- In-place type changes are risky:
  - old readers can treat a compressed `CURRENT_NETWORK` string as truthy and then fail on `.items()`
  - hb-style `NETMON_VERSION + ENCODED_DATA` lets updated readers decode while old readers fail closed by treating `CURRENT_NETWORK` as absent
- The current bandwidth driver is still `CURRENT_NETWORK`, so compressing the whole NET_MON body is effectively compressing the heavy part without a wider format redesign.

## Evidence
- Raw 10-minute SDK sample:
  - `NET_MON_01` = `61.6MB`, `49.0%` of all raw payload bytes
  - `CURRENT_NETWORK` = `59.3MB`, the single largest raw field
  - source: `xperimental/payloads_tests/evidence/raw_bandwidth/20260318T192853+0000_mainnet_bandwidth_results.md`
- NET_MON compression probe on `30` real mainnet samples:
  - average raw `NET_MON_01` size: `347.2KB`
  - `CURRENT_NETWORK`-only compression: `80.2%` payload reduction
  - hb-style NET_MON-body compression: `81.8%` payload reduction
  - projected 10-minute saving from hb-style NET_MON-body compression: `50.4MB`, or `40.1%` of the full raw sample
  - source: `xperimental/payloads_tests/evidence/netmon_compression/20260318T200033+0000_netmon_compression_results.md`

## Minimal Wire Shape

### Existing `v1`
- current top-level NET_MON payload remains unchanged
- `CURRENT_NETWORK`, `CURRENT_ALERTED`, `CURRENT_RANKING`, `WHITELIST_MAP`, `MESSAGE`, and the other non-`EE_*` fields remain top-level

### Proposed `v2`
- keep existing `EE_*` envelope fields unchanged
- add `NETMON_VERSION="v2"`
- add `ENCODED_DATA=<zlib+base64 compressed JSON>`
- put all non-`EE_*` NET_MON business fields inside the encoded body
- keep uncompressed behavior as implicit `v1`

This matches the heartbeat pattern closely:
- sender builds a normal business-body dict
- sender compresses only the bulky body
- receiver expands the body before normal business logic runs

## Core Changes

### 1. Sender toggle and default
- File: `naeural_core/constants.py`
- Add env key constant:
  - `EE_NETMON_COMPRESS_ENV_KEY = "EE_NETMON_COMPRESS"`
- Sender default rule:
  - if `EE_NETMON_COMPRESS` is explicitly set, honor it
  - otherwise default to `ct.ETH_ENABLED`
  - outcome:
    - `EE_ETH_ENABLED=true` -> default compression `ON`
    - `EE_ETH_ENABLED=false` -> default compression `OFF`

Why env-only for phase 1:
- `NET_MON_01` already uses env-driven behavior for cadence/filtering
- this avoids threading a new startup-config key through broader runtime config plumbing
- receiver decode stays unconditional, so mixed fleets can still consume compressed payloads once reader support is shipped
- this is an intentional divergence from heartbeat's startup-config-backed toggle because the requested control surface for this change is an env var and the sender path is plugin-scoped, not orchestrator-global

### 2. Core sender compression point
- File: `naeural_core/business/default/admin/net_mon_01.py`
- Method: `_process`
- Insert immediately after `payload = self._create_payload(...)` and before `return payload`

Planned behavior:
1. Build the normal NET_MON payload object exactly as today.
2. If compression is enabled:
   - derive the filtered wire dict first, using the same boundary that payload sending already uses (`payload.to_dict()` or the equivalent filtered serialization path), not `vars(payload)` / raw `__dict__`
   - split that filtered wire dict into:
     - `EE_*` envelope fields that stay top-level
     - non-`EE_*` NET_MON business fields that move into the encoded body
   - `json.dumps(...)` the inner body
   - `self.log.compress_text(...)`
   - send:
     - `NETMON_VERSION="v2"`
     - `ENCODED_DATA=<compressed body>`
   - do not duplicate raw `CURRENT_NETWORK` when compression is enabled
3. If compression is disabled:
   - keep current wire shape unchanged

Why here:
- whitelist-index compaction already ran before payload creation
- `GeneralPayload` has already added the non-`EE_*` NET_MON metadata fields, and the filtered wire dict excludes internal members such as `owner`, so the compression boundary matches the measured wire shape instead of the raw Python object state
- signing/envelope generation happens after this, so the compressed payload becomes the signed wire payload
- only one sender path is touched

### 3. Core receiver expansion point
- File: `naeural_core/business/default/admin/net_config_monitor.py`
- Method: `netmon_handler`
- Insert before:
  - `current_network = data.get(...)`
  - `maybe_convert_netmon_whitelist(data)`

Planned behavior:
1. Call a helper that expands `NETMON_VERSION="v2"` payloads back into the normal NET_MON dict.
2. Keep all existing whitelist conversion and peer-status logic unchanged.

Why here:
- this is the only in-repo `@payload_handler("NET_MON_01")`
- once expansion runs, current logic can stay as-is
- no admin-pipeline routing or plugin API changes are needed

## SDK Changes

### 4. Shared decode helper
- File: `ratio1/const/payload.py`
- Add a tolerant helper near `maybe_convert_netmon_whitelist(...)`

Suggested shape:
```python
@staticmethod
def maybe_decompress_netmon_payload(full_payload: dict, log) -> dict:
  if full_payload.get(PAYLOAD_DATA.NETMON_VERSION) != "v2":
    return full_payload
  encoded = full_payload.get(PAYLOAD_DATA.ENCODED_DATA)
  if not encoded:
    return full_payload
  decoded = log.decompress_text(encoded)
  if not decoded:
    return full_payload
  body = json.loads(decoded)
  full_payload.pop(PAYLOAD_DATA.ENCODED_DATA, None)
  full_payload.update(body)
  return full_payload
```

Required constants:
- `NETMON_VERSION = "NETMON_VERSION"`
- reuse `ENCODED_DATA`

Important detail:
- mutate the passed dict in place or return the same dict object after update
- do not rebind to a brand-new dict if downstream code still holds references to the original object

### 5. SDK decode insertion point
- File: `ratio1/base/generic_session.py`
- Method: `__maybe_process_net_mon`
- Insert before the first `CURRENT_NETWORK` access

Planned behavior:
1. Inside the existing NET_MON-specific branch, call `PAYLOAD_DATA.maybe_decompress_netmon_payload(dict_msg, self.log)`.
2. Ensure the helper mutates `dict_msg` in place, so later transaction handling, pipeline callbacks, and `custom_on_payload(...)` still see the normalized dict without widening the code touch surface.

Why `__maybe_process_net_mon`:
- it is already the first SDK path that consumes `CURRENT_NETWORK`
- it keeps the change scoped to NET_MON payloads instead of widening generic payload handling
- in-place mutation still lets later callbacks observe the decompressed form

## Rollout Order
1. Ship receiver decode support first:
   - core `net_config_monitor`
   - SDK `__maybe_process_net_mon`
   - shared helper in `ratio1/const/payload.py`
2. Leave sender compression disabled initially.
3. Validate that updated readers accept both:
   - existing uncompressed `v1`
   - compressed `v2`
4. Enable sender compression with `EE_NETMON_COMPRESS=true` in controlled EVM environments.
5. Only after mixed-fleet decode support is confirmed, rely on the default:
   - `EE_ETH_ENABLED=true` -> compression on by default
   - `EE_ETH_ENABLED=false` -> compression off by default

## Critical Compatibility Rules
- Receiver decode must be unconditional when `NETMON_VERSION="v2"` is present.
- Do not gate decode by env.
- Do not duplicate both raw and compressed `CURRENT_NETWORK` on the wire.
- Do not change broker topics, payload signatures, or admin-pipeline routing.
- Do not move compression into comm-layer generic code; keep it localized to NET_MON sender/receiver paths.

## Verification Plan

### Core
- synthetic `v1` NET_MON payload still works unchanged through `net_config_monitor.netmon_handler`
- synthetic `v2` NET_MON payload expands before whitelist conversion and produces the same `current_network` semantics
- EVM path still performs whitelist-map conversion after expansion
- `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=true` -> sender uses compressed path
- `EE_NETMON_COMPRESS` unset + `EE_ETH_ENABLED=false` -> sender uses uncompressed path
- explicit `EE_NETMON_COMPRESS=false` overrides EVM default

### SDK
- synthetic `v2` NET_MON payload injected into `GenericSession.__maybe_process_net_mon` reaches:
  - `__maybe_process_net_mon`
  - pipeline callbacks
  - `custom_on_payload`
  with decompressed `CURRENT_NETWORK`
- heartbeat decode remains unchanged

### Live smoke
- passive listener sees `NETMON_VERSION="v2"` and smaller raw NET_MON payloads when the env is enabled
- updated SDK still reports the same peer/allow-list semantics as before

## Non-Goals
- no topic changes
- no heartbeat redesign
- no generic payload compression framework
- no NET_MON delta protocol in this change
- no UI/API redesign

## Decision
Phase 1 should compress the NET_MON business body in heartbeat style, not replace `CURRENT_NETWORK` in-place. It is the smallest safe change set that:
- captures effectively all measured savings
- keeps the code touch surface narrow
- keeps rollout manageable across core and SDK
