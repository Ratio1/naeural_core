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
- add `NETMON_VERSION="v2"`
- add `ENCODED_DATA=<zlib+base64 compressed JSON>`
- move all non-`EE_*` NET_MON business fields into the encoded body
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
   - move all non-`EE_*` NET_MON business fields into the encoded body
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
