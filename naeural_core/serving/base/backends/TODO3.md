# ThYf8s TRT failure fallback to TorchScript

Main target source code directory: `naeural_core/serving/` with its subdirectories.

## Observations
- `TensorRT build_engine returned None` after `smVerHex2Dig` assertion; RESULT1 shows `Device capability: 12.0` (RTX 5060) with TRT 8.6.1, which does not support SM 12.x and likely triggers the assertion before returning a valid engine.
- `prepare_model` does not catch backend load exceptions, so a TRT failure aborts startup before `ths` can be tried even when the default backend order is `['trt', 'ths']`.
- Logs do not show the resolved backend order or whether `BACKEND` was forced via config, so we cannot confirm whether fallback was intentionally disabled.

## Proposed steps
- [x] Log backend resolution at startup: `cfg_backend`, resolved backend order, and config source (`SERVING_ENVIRONMENT` vs `STARTUP_AI_ENGINE_PARAMS`) so we can see if fallback is allowed and where the config and how was retrieved.
- [x] Add explicit fallback control flags to `UnifiedFirstStage.CONFIG` (e.g., `ALLOW_BACKEND_FALLBACK=True`, `STRICT_BACKEND=False`). If `BACKEND` is set, default `STRICT_BACKEND=True` unless overridden.
- [x] Guard TRT usage before building: detect unsupported SM (e.g., `device_props.major >= 10` with `trt.__version__ < 10`) and skip TRT with a warning so the pipeline can fall back to `ths` without a crash.
- [x] Wrap `load_method` in `UnifiedFirstStage.prepare_model` with try/except; on failure log the exception, mark backend as failed, and continue to the next backend when fallback is allowed.
- [x] Emit a single, high-signal fallback line: `TRT failed (<err>), falling back to ths`, including model name, backend order, and strict/fallback flags.
- [x] Add unit tests: one that simulates a TRT failure and asserts fallback to `ths`, another that asserts strict mode raises, and one that simulates an unsupported SM/TRT combo and asserts TRT is skipped.
- [x] Document a temporary workaround: set `BACKEND="ths"` (or upgrade to TRT 10.x) for `ThYf8s` on GPUs unsupported by the current TRT build.
