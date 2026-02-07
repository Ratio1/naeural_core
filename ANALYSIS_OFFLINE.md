# Offline Execution Analysis (MQTT Down)

## Scope
- Scenario: Edge Node has no external MQTT connectivity.
- Goal: confirm DCT data still reaches business plugins, identify bottlenecks, and document exact flow.

## Assumptions for This Simulation
- Main loop starts successfully and streams/plugins are configured.
- MQTT connection attempts fail repeatedly.
- `WORK_OFFLINE` is explicitly considered in both modes:
  - `WORK_OFFLINE=false` (default risk path)
  - `WORK_OFFLINE=true` (intended offline path)

## Step-by-Step Execution Flow (One Main Loop Iteration)
1. Stream selection  
   `Orchestrator.main_loop` selects active pipelines from config (`choose_current_running_streams`).  
   Reference: `naeural_core/main/orchestrator.py:1818`, `naeural_core/main/orchestrator.py:1820`.

2. Plugin instance refresh  
   `BusinessManager.update_streams` checks/creates plugin instances and deallocates unused ones.  
   Reference: `naeural_core/main/orchestrator.py:1827`, `naeural_core/business/business_manager.py:86`.

3. Serving process management  
   Serving processes are started/stopped based on active AI engine usage.  
   Reference: `naeural_core/main/orchestrator.py:1831`, `naeural_core/main/orchestrator.py:1832`.

4. DCT collection (core data entry point)  
   `CaptureManager.update_streams` then `get_all_captured_data` gathers inputs from capture threads.  
   Reference: `naeural_core/main/orchestrator.py:1835`, `naeural_core/main/orchestrator.py:1326`, `naeural_core/main/orchestrator.py:1329`, `naeural_core/data/capture_manager.py:413`.

5. Data handler update  
   `MainLoopDataHandler` receives:
   - captured DCT data
   - plugin-instance mapping
   - serving mapping  
   Reference: `naeural_core/main/orchestrator.py:1839`, `naeural_core/main/orchestrator.py:1355`.

6. Serving aggregation + inference  
   Captures are aggregated for serving; serving runs in parallel/in-process and outputs are collected.  
   Reference: `naeural_core/main/orchestrator.py:1844`, `naeural_core/main/orchestrator.py:1848`, `naeural_core/serving/serving_manager.py:1237`.

7. DCT data is appended to plugin inputs  
   `append_captures` maps stream captures directly to each plugin instance hash before execution.  
   Reference: `naeural_core/main/orchestrator.py:1854`, `naeural_core/main/main_loop_data_handler.py:169`.

8. Inference outputs appended (if any)  
   Inference data is attached to the same per-plugin input structures.  
   Reference: `naeural_core/main/orchestrator.py:1857`, `naeural_core/main/main_loop_data_handler.py:260`.

9. Plugin execution  
   `BusinessManager.execute_all_plugins` calls `plugin.add_inputs(...)` for each instance.  
   Reference: `naeural_core/main/orchestrator.py:1861`, `naeural_core/business/business_manager.py:667`, `naeural_core/business/base/base_plugin_biz.py:1184`.

10. Comms health check (after plugin execution)  
   Comms failure is evaluated only near loop end:
   - If `WORK_OFFLINE=false` and failures exceed retries, loop exits.
   - If `WORK_OFFLINE=true`, this shutdown path is bypassed.  
   Reference: `naeural_core/main/orchestrator.py:1882`.

## MQTT-Down Simulation Outcome

### A) `WORK_OFFLINE=false`
- MQTT comm threads retry connect (`_nr_conn_retry_iters` increases; sleeps between retries).  
  Reference: `naeural_core/comm/default/mqtt.py:83`, `naeural_core/comm/default/mqtt.py:94`, `naeural_core/comm/default/mqtt.py:99`.
- Once retries exceed `CONN_MAX_RETRY_ITERS`, `comm_failed_after_retries=True`.  
  Reference: `naeural_core/comm/base/base_comm_thread.py:221`.
- Orchestrator triggers shutdown at stage 10.  
  Reference: `naeural_core/main/orchestrator.py:1882`.
- Result: DCT->plugin flow works only until shutdown is triggered.

### B) `WORK_OFFLINE=true`
- Same comm failures happen, but orchestrator does not stop for failed comms.
- Main loop keeps running capture -> data handler -> plugin execution.
- Offline status is periodically logged while failures persist.  
  Reference: `naeural_core/main/orchestrator.py:1627`.
- Result: DCT data continues to reach plugins despite MQTT outage.

## Dataflow Guarantee (DCT -> Plugins) Under Comms Failure
- The DCT-to-plugin path is local in-process logic (capture manager + data handler + business manager).
- No comm dependency gates stages 4->8 in the loop.
- The only comm-dependent kill-switch is the stage-10 check, and it is bypassed by `WORK_OFFLINE=true`.  
  References: `naeural_core/main/orchestrator.py:1835`, `naeural_core/main/orchestrator.py:1854`, `naeural_core/main/orchestrator.py:1861`, `naeural_core/main/orchestrator.py:1882`.

## Bottlenecks and Risks

1. Hard shutdown if offline mode is not enabled  
- Risk: node exits after comm retry threshold even though local processing is otherwise healthy.  
- Key control: `WORK_OFFLINE`.  
- Reference: `naeural_core/main/orchestrator.py:720`, `naeural_core/main/orchestrator.py:1882`.

2. Plugin overload can stop capture ingestion temporarily  
- If plugins are overflown, `CaptureManager` intentionally skips acquisition (`data_flow_stopped`).  
- This is independent of MQTT status and can halt DCT->plugin freshness.  
- Reference: `naeural_core/data/capture_manager.py:96`, `naeural_core/data/capture_manager.py:419`, `naeural_core/data/capture_manager.py:425`.

3. Outgoing comm buffering is bounded (possible message loss while offline)
- Comm buffers are bounded deques (`COMM_SEND_BUFFER=1000`, `COMM_RECV_BUFFER=1000`).
- During long outages, old outgoing messages can be dropped by deque maxlen pressure.
- This affects payload delivery, not local plugin execution.  
- Reference: `naeural_core/comm/base/base_comm_thread.py:110`, `naeural_core/constants.py:1157`.

4. Main-loop startup/refresh cost in BusinessManager
- Plugin discovery/import/safety checks run in the main loop during refresh and can add latency.
- Important during config churn or many unique plugin signatures.  
- Reference: `naeural_core/business/business_manager.py:251`, `naeural_core/business/business_manager.py:295`.

5. Serving-stage latency still blocks plugin run for that iteration
- Serving is executed before plugin execution in each loop.
- If serving is slow/timing out, plugin input consumption is delayed.  
- Reference: `naeural_core/main/orchestrator.py:1848`, `naeural_core/serving/serving_manager.py:1348`.

## Practical Conclusion
- To keep DCT telemetry and plugin processing alive with external MQTT down, `WORK_OFFLINE` must be `true`.
- In that mode, local DCT->plugin flow is preserved by design.
- Remaining practical bottlenecks are plugin queue overload, serving latency, and bounded outgoing comm buffering (delivery loss, not processing loss).

