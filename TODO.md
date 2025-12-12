# ThYf8s TensorRT build failure

## What is happening
- Startup path: `ThYf8s` -> `YfBase._get_model` -> `BasicTh.prepare_model` selects the TRT backend, downloads `20240430_y8s_640x1152_nms_f32.onnx`, and calls `TensorRTModel.load_or_rebuild_model`.
- No cached engine passes `check_engine_file`, so `_prepare_trt_model` rebuilds via `naeural_core/serving/base/backends/trt.py:create_from_onnx`.
- TensorRT logs: INT64 weights are being cast/clamped to INT32; EfficientNMS_TRT plugin is found; TF32 is disabled because the GPU lacks support; then `[helpers.h::smVerHex2Dig::694] Assertion major >= 0 && major < 10 failed` indicates a GPU/arch version mismatch during engine creation.
- The Python build step wraps `builder.build_engine(network, config)` in a context manager, but `ICudaEngine` is not a context manager in TRT 8/9, so `__enter__` is missing. That raises `AttributeError: __enter__`, which is re-raised as `RuntimeError: Failed to build TensorRT engine: __enter__`; no `.engine` or metadata is written and the serving crashes during startup.

## Action plan
- [ ] Fix `naeural_core/serving/base/backends/trt.py:create_from_onnx` to call `builder.build_engine` (and `build_serialized_network`) without a context manager, check for `None`, and surface clear build failures (include the `smVerHex2Dig` hint).
- [ ] Log GPU name + compute capability + TensorRT/CUDA versions before building; fail fast or warn when the detected SM major is not supported by the current TensorRT build (the `smVerHex2Dig` assertion).
- [ ] Revisit the ONNX export for YF8s (`20240430_y8s_640x1152_nms_f32.onnx`): remove INT64 weights or explicitly cast to INT32, and confirm EfficientNMS_TRT is packaged/available on the target machine.
- [ ] After code fixes, rebuild the TRT engine on target hardware and keep the generated `.engine` and `.engine.json` under `<models>/trt/<model>/<precision>/bs<batch>` so subsequent startups load from cache.
- [ ] Add a lightweight regression test (mocking the TensorRT builder/runtime) under `naeural_core/business/test_framework` to cover `create_from_onnx` API usage, preventing future `__enter__` regressions when TensorRT APIs shift.
- [ ] Update docs/release notes to record supported TensorRT/GPU combinations for ThYf8s and note that TF32 may be disabled on older cards during build.
