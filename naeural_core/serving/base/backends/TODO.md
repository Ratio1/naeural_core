# ThYf8s TensorRT build failure

## Error log
```
[TH_YF8S][25-12-22 10:26:11][NumpySharedMemory] Initializing shmem 'TH_YF8S' using multiprocessing.SharedMemory
[TH_YF8S][25-12-22 10:26:11][NumpySharedMemory] Shared memory 'TH_YF8S'[User] successfully initialized
[TH_YF8S][25-12-22 10:26:11][282] Updating model config with 'SERVING_ENVIRONMENT' config...
[TH_YF8S][25-12-22 10:26:11][282] Updating model config with upstream config...
[TH_YF8S][25-12-22 10:26:11][282] Automatic validation for instance of `ThYf8s` is successful
[TH_YF8S][25-12-22 10:26:11][282] Model setup initiated for ThYf8s
[TH_YF8S][25-12-22 10:26:11][282] Prepping classes if available...
[TH_YF8S][25-12-22 10:26:11] Maybe dl '['coco.txt']' to 'models' from '[None]'
[TH_YF8S][25-12-22 10:26:11] Cannot download 'coco.txt' from 'None'
[TH_YF8S][25-12-22 10:26:11] Loading json 'coco.txt' from 'models'
[TH_YF8S][25-12-22 10:26:11]   File not found!
[TH_YF8S][25-12-22 10:26:11][282] WARNING: based class names loading failed. This is not necessarily an error as classes can be loaded within models.
[TH_YF8S][25-12-22 10:26:11][282] Loading for backend trt
[TH_YF8S][25-12-22 10:26:11][282] Preparing TH_YF8S TensorRT model 0.0.0...
[TH_YF8S][25-12-22 10:26:11] Maybe dl '['trt/20240430_y8s_640x1152_nms_f32_trt/20240430_y8s_640x1152_nms_f32_trt.onnx']' to 'models' from '['minio:Y8/20240430_y8s_640x1152_nms_f32_trt.onnx']'
[TH_YF8S][25-12-22 10:26:11] File 20240430_y8s_640x1152_nms_f32_trt.onnx found. Skipping.
[TH_YF8S][25-12-22 10:26:11][282] Using ONNX model 20240430_y8s_640x1152_nms_f32_trt.onnx (42.959 MB) at `./_local_cache/_models/trt/20240430_y8s_640x1152_nms_f32_trt/20240430_y8s_640x1152_nms_f32_trt.onnx` using map_location: cuda:0 on python v3.10.12...
[TH_YF8S][25-12-22 10:26:11][282] Trying to load TensorRT model from ./_local_cache/_models/trt/20240430_y8s_640x1152_nms_f32_trt/20240430_y8s_640x1152_nms_f32_trt.onnx
[TH_YF8S][25-12-22 10:26:11][TRT] No metadata file
[TH_YF8S][25-12-22 10:26:11][TRT] Failed to load model, trying to rebuild
[TH_YF8S][25-12-22 10:26:11] Building TensorRT engine file, this can take up to an hour
[EE][25-12-22 10:26:12][SMGR] Serving process 'TH_YF8S' is running with PID 282
[EE][25-12-22 10:26:12][SMGR]   Waiting until 'TH_YF8S' responds
[TH_YF8S][25-12-22 10:26:12][TRT] [MemUsageChange] Init CUDA: CPU +29, GPU +0, now: CPU 231, GPU 239 (MiB)
[TH_YF8S][25-12-22 10:26:14][TRT] [MemUsageChange] Init builder kernel library: CPU +1, GPU +0, now: CPU 308, GPU 239 (MiB)
[TH_YF8S][25-12-22 10:26:14][TRT] ----------------------------------------------------------------
[TH_YF8S][25-12-22 10:26:14][TRT] Input filename:   _local_cache/_models/trt/20240430_y8s_640x1152_nms_f32_trt/20240430_y8s_640x1152_nms_f32_trt.tofp16.onnx
[TH_YF8S][25-12-22 10:26:14][TRT] ONNX IR version:  0.0.7
[TH_YF8S][25-12-22 10:26:14][TRT] Opset version:    14
[TH_YF8S][25-12-22 10:26:14][TRT] Producer name:    pytorch
[TH_YF8S][25-12-22 10:26:14][TRT] Producer version: 2.0.1
[TH_YF8S][25-12-22 10:26:14][TRT] Domain:
[TH_YF8S][25-12-22 10:26:14][TRT] Model version:    0
[TH_YF8S][25-12-22 10:26:14][TRT] Doc string:
[TH_YF8S][25-12-22 10:26:14][TRT] ----------------------------------------------------------------
[TH_YF8S][25-12-22 10:26:14][TRT] onnx2trt_utils.cpp:374: Your ONNX model has been generated with INT64 weights, while TensorRT does not natively support INT64. Attempting to cast down to INT32.
[TH_YF8S][25-12-22 10:26:14][TRT] onnx2trt_utils.cpp:400: One or more weights outside the range of INT32 was clamped
[TH_YF8S][25-12-22 10:26:14][TRT] Tensor DataType is determined at build time for tensors not marked as input or output.
[TH_YF8S][25-12-22 10:26:14][TRT] No importer registered for op: EfficientNMS_TRT. Attempting to import as plugin.
[TH_YF8S][25-12-22 10:26:14][TRT] Searching for plugin: EfficientNMS_TRT, plugin_version: 1, plugin_namespace:
[TH_YF8S][25-12-22 10:26:14][TRT] Successfully created plugin: EfficientNMS_TRT
[TH_YF8S][25-12-22 10:26:14][TRT] BuilderFlag::kTF32 is set but hardware does not support TF32. Disabling TF32.
[TH_YF8S][25-12-22 10:26:14][TRT] 2: [helpers.h::smVerHex2Dig::694] Error Code 2: Internal Error (Assertion major >= 0 && major < 10 failed. )
[TH_YF8S][25-12-22 10:26:14][282] Exception in ThYf8s:
dec 22 08:26:14 teste sh[3752535]: Traceback (most recent call last):
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/backends/trt.py", line 735, in create_from_onnx
dec 22 08:26:14 teste sh[3752535]:     with builder.build_engine(network, config) as engine, open(path, 'wb') as t:
dec 22 08:26:14 teste sh[3752535]: AttributeError: __enter__
dec 22 08:26:14 teste sh[3752535]: During handling of the above exception, another exception occurred:
dec 22 08:26:14 teste sh[3752535]: Traceback (most recent call last):
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/base_serving_process.py", line 601, in run
dec 22 08:26:14 teste sh[3752535]:     _msg = self.__startup()
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/base_serving_process.py", line 713, in __startup
dec 22 08:26:14 teste sh[3752535]:     return self._startup()
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 871, in _startup
dec 22 08:26:14 teste sh[3752535]:     msg = self._setup_model()
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 299, in _setup_model
dec 22 08:26:14 teste sh[3752535]:     self.th_model = self._get_model(config=backend_model_map)
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/default_inference/th_yf_base.py", line 74, in _get_model
dec 22 08:26:14 teste sh[3752535]:     model, model_loaded_config, fn = self.prepare_model(
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 584, in prepare_model
dec 22 08:26:14 teste sh[3752535]:     model, config = load_method(
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/mixins_base/trt_mixin.py", line 61, in _prepare_trt_model
dec 22 08:26:14 teste sh[3752535]:     model.load_or_rebuild_model(fn_path, half, max_batch_size, self.dev)
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/backends/trt.py", line 439, in load_or_rebuild_model
dec 22 08:26:14 teste sh[3752535]:     TensorRTModel.create_from_onnx(
dec 22 08:26:14 teste sh[3752535]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/backends/trt.py", line 742, in create_from_onnx
dec 22 08:26:14 teste sh[3752535]:     raise RuntimeError(f"Failed to build TensorRT engine: {e}")
dec 22 08:26:14 teste sh[3752535]: RuntimeError: Failed to build TensorRT engine: __enter__
dec 22 08:26:14 teste sh[3752535]:
```

## What is happening
- Startup path: `ThYf8s` -> `YfBase._get_model` -> `BasicTh.prepare_model` selects the TRT backend, downloads `20240430_y8s_640x1152_nms_f32_trt.onnx`, and calls `TensorRTModel.load_or_rebuild_model` (the build uses the derived `.tofp16.onnx` when fp16 is enabled).
- No cached engine passes `check_engine_file`, so `_prepare_trt_model` rebuilds via `naeural_core/serving/base/backends/trt.py:create_from_onnx`.
- TensorRT logs: INT64 weights are being cast/clamped to INT32; EfficientNMS_TRT plugin is found; TF32 is disabled because the GPU lacks support; then `[helpers.h::smVerHex2Dig::694] Assertion major >= 0 && major < 10 failed` suggests a device/driver/arch mismatch or failed capability query, but we need more build diagnostics to confirm root cause.
- The Python build step wraps `builder.build_engine(network, config)` in a context manager, but `ICudaEngine` is not a context manager in TRT 8/9, so `__enter__` is missing. That raises `AttributeError: __enter__`, which is re-raised as `RuntimeError: Failed to build TensorRT engine: __enter__`; no `.engine` or metadata is written and the serving crashes during startup.

## Action plan
- [x] Fix `naeural_core/serving/base/backends/trt.py:create_from_onnx` to call `builder.build_engine` (and `build_serialized_network`) without a context manager, check for `None`, and surface clear build failures (include the `smVerHex2Dig` hint).
- [x] Expand TRT build diagnostics by ~5x: log GPU name, device index, compute capability, CUDA driver/runtime, Torch/TRT versions, ONNX size/md5, builder flags, optimization profile shapes, and ONNX parser errors; enable verbose TRT logging during build so GPU/TRT issues are traceable.
- [ ] Revisit the ONNX export for YF8s (`20240430_y8s_640x1152_nms_f32_trt.onnx` and its `.tofp16` derivative): remove INT64 weights or explicitly cast to INT32, and confirm EfficientNMS_TRT is packaged/available on the target machine.
- [ ] After code fixes, rebuild the TRT engine on target hardware and verify the generated `.engine` and `.engine.json` land under `<models>/trt/<model>/<precision>/bs<batch>` so subsequent startups load from cache (this is the default path today).
- [x] Add a lightweight regression test (mocking the TensorRT builder/runtime) under `naeural_core/business/test_framework` to cover `create_from_onnx` API usage, preventing future `__enter__` regressions when TensorRT APIs shift.
- [x] Update docs/release notes to record supported TensorRT/GPU combinations for ThYf8s and note that TF32 may be disabled on older cards during build.


