# TensorRT Serving Notes

## Compatibility and caching
- Engine compatibility is tied to TensorRT version, CUDA version, and GPU name; a mismatch forces a rebuild.
- Engine artifacts are written under `<onnx_dir>/<precision>/bs<batch>/` as `.engine` and `.engine.json`.

## Supported combinations (ThYf8s)
- ThYf8s TRT builds currently expect the `EfficientNMS_TRT` plugin; per NVIDIA, it is deprecated in TensorRT 10.12.
- Until exports move to `INMSLayer`, prefer TensorRT versions that still ship `EfficientNMS_TRT` (<= 10.11).
- GPU/driver combinations should follow the NVIDIA TensorRT support matrix for the selected TensorRT release.

## Build/runtime signals to capture
- Record GPU name, compute capability, driver version, CUDA runtime, Torch/TensorRT versions, and ONNX md5/size.
- Save ONNX parser errors/warnings and the optimization profile shapes used at build time.

## TF32 expectations
- TF32 Tensor Core support is an Ampere-era feature; older GPUs will log TF32 disabled during build.
- If TF32 is expected but disabled, confirm GPU architecture and driver/runtime alignment.

## EfficientNMS_TRT plugin
- `EfficientNMS_TRT` is part of the TensorRT plugin library and is deprecated in recent TensorRT releases.
- On systems where the plugin is missing, ensure `libnvinfer_plugin` is available and plugins are initialized.
- Prefer `INMSLayer` for new exports targeting TensorRT 10.12+.

## INT64 weights in ONNX
- TensorRT ONNX parsing will downcast INT64 weights to INT32; values outside INT32 range are clamped.
- Treat these warnings as export issues and regenerate ONNX with INT32 constants when possible.

## References
- https://docs.nvidia.com/deeplearning/tensorrt/latest/_static/python-api/infer/Core/Builder.html
- https://docs.nvidia.com/cuda/ampere-tuning-guide/index.html
- https://raw.githubusercontent.com/NVIDIA/TensorRT/main/plugin/efficientNMSPlugin/README.md
- https://forums.developer.nvidia.com/t/downcasting-from-int64-to-int32/111149
- https://forums.developer.nvidia.com/t/rtx-3070-tensorrt-internal-error-assertion-failed-unsupported-sm/169830
- https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html
