# Serving Troubleshooting

## TensorRT fallback
If TensorRT fails to build on newer GPUs (for example, SM 12.x with TensorRT 8.x),
force TorchScript or upgrade TensorRT:

- Set `BACKEND="ths"` for the affected serving (for example, ThYf8s).
- Upgrade to TensorRT 10.x with a compatible CUDA and driver stack.
