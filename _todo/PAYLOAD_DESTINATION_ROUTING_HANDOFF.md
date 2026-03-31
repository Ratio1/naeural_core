# Payload Destination Routing Handoff

Date: `2026-03-25`

## Goal
Analyze and refine a cross-repo change so payloads are no longer always broadcast on the generic payload topic.

Desired behavior:
- if `EE_DESTINATION` is `None`, keep broadcast behavior
- if `EE_DESTINATION` is a single receiver or list of receivers, publish the same payload on each receiver-specific payload subtopic
- example:
  - current: `naeural/payloads`
  - desired for `EE_DESTINATION=[ADDR1, ADDR2]`: `naeural/ADDR1/payloads` and `naeural/ADDR2/payloads`

This handoff is for deeper analysis and refinement before implementation.

## Current High-Confidence Findings

### `naeural_core` send path
- [communication_manager.py](/workspaces/naeural_core/naeural_core/comm/communication_manager.py#L304) is called by [orchestrator.py](/workspaces/naeural_core/naeural_core/main/orchestrator.py#L1565) for `PAYLOAD`, `COMMAND`, `NOTIFICATION`, and `HEARTBEAT`.
- For payloads, `CommunicationManager.send()` wraps the payload and sends it to the `DEFAULT` communicator.
- [default_comm_mixin.py](/workspaces/naeural_core/naeural_core/comm/mixins/default_comm_mixin.py#L16) dequeues one logical payload, calls `_prepare_message(msg, msg_id)`, then `send_wrapper(msg)`.
- [base_comm_thread.py](/workspaces/naeural_core/naeural_core/comm/base/base_comm_thread.py#L608) `_prepare_message()` already reads `EE_DESTINATION` and preserves it in the outgoing payload. It also performs payload encryption using `receiver_address=destination_addr`, including list destinations.
- [base_comm_thread.py](/workspaces/naeural_core/naeural_core/comm/base/base_comm_thread.py#L380) `send_wrapper()` signs, JSON-serializes, then calls transport `_send(message, send_to=send_to)`.
- [mqtt.py](/workspaces/naeural_core/naeural_core/comm/default/mqtt.py#L154) delegates to `ratio1.comm.MQTTWrapper.send(...)`.

### Current repo config means payloads are broadcast
- [.config_app.json](/workspaces/naeural_core/.config_app.json#L39) sets `PAYLOADS_CHANNEL.TOPIC` to `naeural/payloads`.
- There is no `{}` placeholder in the payload topic, so current runtime payload publish is broadcast-only.

### `ratio1` already has partial addressed-topic support, but not for payloads
- [base_comm_wrapper.py](/workspaces/ratio1/ratio1/comm/base_comm_wrapper.py#L172) supports templated send topics through `get_send_channel_def(send_to=...)`, but that handles one `send_to`, not many.
- [mqtt_session.py](/workspaces/ratio1/ratio1/default/session/mqtt_session.py#L109) explicitly hardcodes payload send as broadcast:
  - `_send_payload()` calls `_send_raw_message(to=None, ...)`
  - comment says payload segregation is not implemented yet
- [mqtt_session.py](/workspaces/ratio1/ratio1/default/session/mqtt_session.py#L97) also notes multiple receivers are not supported.

### Receive-side coupling exists in both repos
- [base_iot_queue_listener.py](/workspaces/naeural_core/naeural_core/data/base/base_iot_queue_listener.py#L106) subscribes to `PAYLOADS_CHANNEL.TOPIC` from shared comm config.
- [base_iot_queue_listener.py](/workspaces/naeural_core/naeural_core/data/base/base_iot_queue_listener.py#L119) builds a wrapper config with `EE_ID`, but not `EE_ADDR`.
- sibling [base_comm_wrapper.py](/workspaces/ratio1/ratio1/comm/base_comm_wrapper.py#L147) expands templated receive topics using `get_subtopic_values()`, which returns `[EE_ID, EE_ADDR]` in alias mode and `[EE_ADDR]` otherwise.
- sibling [mqtt_wrapper.py](/workspaces/ratio1/ratio1/comm/mqtt_wrapper.py#L91) subscribes to all resolved topics for a templated receive topic.

## Important Architectural Conclusion
This is not a safe one-line change in `CommunicationManager.send()`.

If implementation happens too high in the stack by calling `.send()` once per receiver:
- `_prepare_message()` runs multiple times
- encryption may run multiple times
- signing runs multiple times
- JSON serialization runs multiple times
- `EE_MESSAGE_ID` / hash / signature can differ per receiver

That is the wrong place to fan out.

The right general design is:
- normalize and dedupe destinations once
- prepare the payload once
- sign and serialize once
- publish the same serialized bytes to each destination topic

This suggests the fanout belongs after `_prepare_message()` and before the transport primitive, not at orchestrator or `CommunicationManager` level.

Recommended layering:
- `send_wrapper()` remains the "prepare/sign/jsonify once" boundary
- `send_wrapper()` calls a payload-routing helper with the serialized message plus routing metadata
- the helper resolves one or many target topics, calls the existing single-topic transport send primitive once per topic, and aggregates the result
- keep the low-level `_send(...)` primitive single-topic unless a later caller proves a shared `_send_many(...)` abstraction is needed

Important comm-semantics note:
- current [base_comm_thread.py](/workspaces/naeural_core/naeural_core/comm/base/base_comm_thread.py#L395) assumes one transport send and one byte-count update
- payload fanout should therefore return a structured aggregate publish result rather than silently hiding multi-topic publish behind the old integer-only success path
- stats should distinguish one logical payload from N transport publishes so broker load and payload throughput are not conflated

## Current Design Constraints

### Single topic field is insufficient
Current transport config gives one payload topic definition:
- broadcast example: `naeural/payloads`
- targeted example: `naeural/{}/payloads`

One field cannot express both cleanly.

If `PAYLOADS_CHANNEL.TOPIC` becomes templated:
- targeted send becomes possible
- broadcast send with `send_to=None` no longer has a valid resolved topic

If `PAYLOADS_CHANNEL.TOPIC` stays fixed:
- broadcast remains fine
- targeted send is impossible without extra transport logic

Conclusion:
- either add a second explicit payload topic definition for targeted send
- or extend wrapper logic to know both the generic topic and the targeted topic shape

## Working Recommendation
Preferred migration design:
- keep broadcast payload topic for `EE_DESTINATION is None`
- add a separate targeted payload topic template for directed payloads
- keep one logical payload communicator; do not create a second payload comm instance just for addressed topics
- subscribe to both broadcast and addressed payload topics on receive by default
- add an environment flag to disable addressed payload subscription as a rollout/rollback safety lever

Candidate config shape:
- `PAYLOADS_CHANNEL.TOPIC`: `naeural/payloads`
- new field or sibling channel for targeted payloads: `naeural/{}/payloads`
- payload receive can continue to use the same logical channel config, but the wrapper/topic-resolution path should treat the targeted topic as additive rather than reinterpreting all channels as `TOPIC` + `TARGETED_TOPIC`

Candidate transport behavior:
- no destination: publish once to generic topic
- one or more destinations: publish same serialized bytes to each unique targeted topic

Candidate receive behavior:
- always subscribe to broadcast payload topic
- also subscribe to own addressed payload topic by default
- allow an env flag such as `EE_DISABLE_ADDRESSED_PAYLOAD_SUBS=true` to force broadcast-only receive during rollout or rollback
- inject `EE_ADDR` into listener/session comm config so addressed topic expansion has the local node address available

## Optimization Guidance
For MQTT, the efficient approach is:
- one logical payload
- one `_prepare_message()`
- one sign/hash step
- one JSON serialization
- N `mqttc.publish()` calls on the same connected client

Avoid:
- rebuilding the outgoing payload N times
- reconnecting per publish
- helper APIs that bypass the existing long-lived client object

Additional notes:
- normalize non-prefixed addresses to canonical addresses before topic expansion
- dedupe repeated destinations while preserving order
- if aliases are allowed as input, resolve them before topic publish

## Areas That Need Further Analysis / Refinement

1. `ratio1` source checkout
- Analysis is now based on the sibling `/workspaces/ratio1` checkout rather than an installed package path.
- Confirm whether the active runtime environment matches the sibling repo checkout before implementation.

2. Exact receive-side migration plan
- Default receive plan is now:
  - subscribe to generic broadcast payload topic
  - subscribe to own addressed payload topic
  - allow an env flag to disable addressed payload subscription and fall back to broadcast-only receive
- Confirm the exact env/config ownership for that flag in both repos.
- Confirm whether any current consumers still rely on addressed payloads being duplicated onto the generic broadcast topic.

3. Wrapper API shape
- Preferred direction:
  - keep `send_wrapper()` as the helper boundary above transport send
  - keep the low-level transport `_send(...)` primitive single-topic by default
  - let a payload-routing helper resolve one-or-many targets and call `_send(...)` once per topic
- Avoid widening existing `send_to` semantics for command/config/notif flows unless later evidence shows a shared multi-destination API is genuinely needed.
- Introduce `_send_many(...)` only if a second real caller appears and the abstraction pays for itself.

4. Config ownership and compatibility
- Decide whether the new targeted payload topic belongs in:
  - shared comm config
  - a new channel entry
  - or transport-specific config
- Preserve existing command/config/notif topic semantics.
- Do not let `TARGETED_TOPIC` change the behavior of existing channels that already use templated `TOPIC` values such as config routing.

5. AMQP scope
- Treat this task as MQTT-only unless a later requirement explicitly re-expands scope.
- AMQP appears not to support equivalent per-publish retargeting with current code shape.
- Do not spend implementation effort preserving AMQP parity for this change unless required.

6. MQTT publish capabilities and wrapper shape
- The practical implementation should treat multi-topic fanout as repeated `mqttc.publish(topic=..., payload=..., qos=...)` calls on the same connected client, not as a single atomic multi-topic publish.
- Prefer a payload-routing helper called by `send_wrapper()` after one prepare/sign/jsonify pass.
- Keep existing single-destination command behavior unchanged.
- Do not widen wrapper-level `send_to` semantics or add `_send_many(...)` unless a later concrete need justifies it.

7. Failure and retry semantics
- Multi-topic publish is not atomic.
- Need to decide retry behavior if publish succeeds for some destinations and fails for others.
- Existing message IDs/hashes may reduce duplicate-processing risk, but this should be explicitly reasoned about.
- Prefer retrying only the failed destination topics using the already serialized payload rather than rebuilding the payload.
- Decide how outgoing comm stats should record:
  - one logical payload
  - N attempted publishes
  - N successful publishes
  - total published bytes

## Suggested Next Investigation Steps

1. Open the cross-repo devcontainer and inspect sibling `ratio1` source directly.
2. Trace `ratio1` payload receive subscriptions in the repo checkout and confirm how node address is injected into session config.
3. Search for all places that subscribe to payload topics in both repos.
4. Propose the minimal config change that supports both broadcast and targeted payload topics while keeping existing templated channels unchanged.
5. Treat repeated `publish()` calls on one connected client as the MQTT fanout implementation model; do not assume any single-call multi-topic publish primitive exists.
6. Implement fanout as a helper called by `send_wrapper()` after one prepare/sign/jsonify pass; keep the low-level transport send primitive single-topic unless later evidence shows `_send_many(...)` is worth introducing.
7. Check whether any current consumers rely on receiving addressed payloads from the generic broadcast topic even when they are not recipients.
8. Finalize the rollout as dual-subscribe by default with an env flag to disable addressed subscriptions if needed.

## Implementation Plan

1. Config and constants
- Add an additive targeted payload topic field for payload channels, keeping the existing broadcast `TOPIC` unchanged.
- Add a narrowly scoped env/config flag to disable addressed payload subscriptions during rollout, with the working name `EE_DISABLE_ADDRESSED_PAYLOAD_SUBS`.
- Thread the new topic field and disable flag through both repos without changing existing config/config-control/notif channel behavior.

2. `naeural_core` send path
- Extend [base_comm_thread.py](/workspaces/naeural_core/naeural_core/comm/base/base_comm_thread.py) so `send_wrapper()` still performs `_prepare_message()`, signing, and JSON serialization once.
- Add a payload-routing helper above the low-level transport primitive that:
  - resolves broadcast vs addressed payload targets from `EE_DESTINATION`
  - normalizes and dedupes destination addresses while preserving order
  - calls the single-topic `_send(...)` once per resolved topic
  - returns an aggregate publish result including attempted topics, successful topics, failed topics, and published bytes
- Keep command/heartbeat/notification behavior unchanged.

3. `naeural_core` receive path
- Update [base_iot_queue_listener.py](/workspaces/naeural_core/naeural_core/data/base/base_iot_queue_listener.py) to inject `EE_ADDR` into the wrapper config alongside `EE_ID`.
- Teach payload receive-topic assembly to subscribe to broadcast payload topic and, by default, the node's addressed payload topic.
- Honor the disable flag so operators can force broadcast-only receive without reverting the publish-side change.

4. `ratio1` wrapper and session path
- Extend the sibling MQTT wrapper/base wrapper to understand an additive targeted payload topic field only for payload routing.
- Keep existing templated `TOPIC` handling intact for channels such as config routing.
- Update [mqtt_session.py](/workspaces/ratio1/ratio1/default/session/mqtt_session.py) to:
  - keep payload send on one prepare/jsonify path
  - route addressed payloads through the helper logic rather than hardcoding `to=None`
  - subscribe to both broadcast and addressed payload topics by default
  - honor the disable flag for broadcast-only fallback

5. Telemetry and failure semantics
- Treat fanout as one logical payload and N transport publishes.
- Update outgoing comm accounting to use aggregate publish results rather than assuming one publish per payload.
- On partial failure, report the failed topics explicitly and retry only the failed destinations using the already serialized payload where practical.

6. Verification
- Docs/config changes: manual diff and path sanity check.
- `naeural_core` Python changes: `python -m compileall` on touched `naeural_core` paths.
- `ratio1` Python changes: `python -m compileall` on touched sibling `ratio1` paths.
- If a safe live MQTT reproducer is not available in this environment, record directed-payload integration validation as blocked rather than implied.

## Review Of Implementation Plan
- The plan keeps one logical payload communicator and avoids queue/reconnect duplication.
- The plan keeps `_send(...)` single-topic, which avoids breaking current command routing semantics.
- The plan makes addressed payload receive opt-out rather than opt-in, but keeps a rollback lever through the disable flag.
- The main remaining implementation risk is partial publish failure handling; verification should explicitly review that area after code changes.

## Preliminary Implementation Direction

Most likely write scopes, if implementation starts:
- `naeural_core/comm/base/base_comm_thread.py`
- `naeural_core/comm/default/mqtt.py`
- `naeural_core/comm/communication_manager.py` only if normalization helpers are needed, not for fanout
- `naeural_core/data/base/base_iot_queue_listener.py`
- `.config_app.json` or the effective shared comm config source used in deployment
- `AGENTS.md` if implementation introduces a new operator-visible env flag or changes verification/operator behavior
- sibling `ratio1`:
  - `ratio1/comm/base_comm_wrapper.py`
  - `ratio1/comm/...mqtt wrapper...`
  - `ratio1/default/session/mqtt_session.py`
  - maybe `ratio1/base/generic_session.py`

## Explicit Warnings
- Do not implement per-destination fanout by calling `CommunicationManager.send()` multiple times for the same logical payload.
- Do not assume the active runtime package and the sibling `ratio1` repo checkout are identical until verified.
- Do not change payload topic config to templated-only without a receive-side compatibility plan.
- Do not create a second payload comm instance just to separate broadcast and addressed payload topics; keep one logical payload communicator and handle routing inside its publish/subscribe helper path.
- Do not let a new payload `TARGETED_TOPIC` field change the behavior of existing channels that already use templated `TOPIC` routing.
- Do not spend effort preserving or extending AMQP behavior for this task unless a requirement explicitly demands it.

## Useful References Already Reviewed
- [communication_manager.py](/workspaces/naeural_core/naeural_core/comm/communication_manager.py)
- [base_comm_thread.py](/workspaces/naeural_core/naeural_core/comm/base/base_comm_thread.py)
- [default_comm_mixin.py](/workspaces/naeural_core/naeural_core/comm/mixins/default_comm_mixin.py)
- [mqtt.py](/workspaces/naeural_core/naeural_core/comm/default/mqtt.py)
- [base_iot_queue_listener.py](/workspaces/naeural_core/naeural_core/data/base/base_iot_queue_listener.py)
- [base_decentrai_connector.py](/workspaces/naeural_core/naeural_core/data/base/base_decentrai_connector.py)
- sibling repo:
  - [/workspaces/ratio1/ratio1/comm/base_comm_wrapper.py](/workspaces/ratio1/ratio1/comm/base_comm_wrapper.py)
  - [/workspaces/ratio1/ratio1/comm/mqtt_wrapper.py](/workspaces/ratio1/ratio1/comm/mqtt_wrapper.py)
  - [/workspaces/ratio1/ratio1/default/session/mqtt_session.py](/workspaces/ratio1/ratio1/default/session/mqtt_session.py)
  - [/workspaces/ratio1/ratio1/base/generic_session.py](/workspaces/ratio1/ratio1/base/generic_session.py)

## Handoff Envelope
```yaml
task_id: COMM-20260325-DEST-PAYLOADS
attempt: 1
owner_role: comm-owner
goal: Design targeted payload MQTT publish using EE_DESTINATION while preserving safe broadcast behavior and cross-repo compatibility.
current_status: working
changed_files:
  - _todo/PAYLOAD_DESTINATION_ROUTING_HANDOFF.md
tests_run:
  - command: manual diff review and path sanity check
    result: pass
    evidence: updated content reviewed manually; some old path references were corrected in this revision
  - command: python3 -c "import pathlib,sys; print(pathlib.Path('_todo/PAYLOAD_DESTINATION_ROUTING_HANDOFF.md').exists())"
    result: pass
evidence_reviewed:
  - naeural_core comm send path
  - naeural_core payload listener path
  - sibling ratio1 MQTT wrapper/session path
  - current app config payload topic definitions
open_risks:
  - retry and partial-failure semantics for multi-topic publish are not finalized
  - exact operator/config ownership for the addressed-subscription env flag is not finalized
  - AMQP scope intentionally unresolved
next_recommended_action: implement the helper-based payload fanout and dual-subscribe rollout in both repos, including EE_ADDR injection and an env flag to disable addressed payload subscriptions if needed
```
