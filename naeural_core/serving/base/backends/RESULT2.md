[TH_YF8S][26-01-14 13:04:11]   File not found!
[TH_YF8S][26-01-14 13:04:11][52] WARNING: based class names loading failed. This is not necessarily an error as classes can be loaded within models.
[TH_YF8S][26-01-14 13:04:11][52] Loading for backend trt
[TH_YF8S][26-01-14 13:04:11][52] TRT failed (TensorRT 8.6.1 does not support SM 12.0), falling back to ths. model=TH_YF8S, backend_order=['trt', 'ths'], allow_fallback=True, strict_backend=False
[TH_YF8S][26-01-14 13:04:11][52] Loading for backend ths
[TH_YF8S][26-01-14 13:04:11][52] Preparing TH_YF8S torchscript graph model 0.0.0...
[TH_YF8S][26-01-14 13:04:11] Maybe dl '['20240305_y8s_640x1152_nms.ths']' to 'models' from '['minio:Y8/20240305_y8s_640x1152_nms.ths']'
[TH_YF8S][26-01-14 13:04:11] Creating secure connection to Minio (secure(<class 'bool'>): True)...
[TH_YF8S][26-01-14 13:04:11] Downloading from Minio: <0mJZ1sJNQzN9x2rAKJ4Z Ce1dHJrSPwdhgVjneWsSG9S9FncBBTPkKsnu5gZJ @minio-pre.hyperfy.tech>, secure:True, SSL_CERT_FILE:'None', cert_reqs:'CERT_NONE' using http_client: <urllib3.poolmanager.PoolManager object at 0x7b79608e5360>...
[EE][26-01-14 13:04:11][SMGR] Serving process 'TH_YF8S' is running with PID 52
[EE][26-01-14 13:04:11][SMGR]   Waiting until 'TH_YF8S' responds
[EE][26-01-14 13:04:14][NMON] Box alive: 0xai_AjsKV3Dy55oLIXg2UqOQrzstiDTt7nsuKgHgDdNdFo0J:pre-k8s-super-0.
[EE][26-01-14 13:04:15][NMON] Box alive: 0xai_A71I2rXUmTUqumBSsiD7ebZk9J4qRmwvC9i3JVElRgxx:pre-k8s-super-1.
[TH_YF8S][26-01-14 13:04:15] Downloaded './_local_cache/_models/20240305_y8s_640x1152_nms.ths' from minio-pre.hyperfy.tech/model-zoo/Y8/20240305_y8s_640x1152_nms.ths in 4.14s
[TH_YF8S][26-01-14 13:04:15][52] Loading torchscript model 20240305_y8s_640x1152_nms.ths (43.075 MB) at `./_local_cache/_models/20240305_y8s_640x1152_nms.ths` using map_location: cuda:0 on python v3.10.12...
[TH_YF8S][26-01-14 13:04:15][52] Done loading model on device cuda:0
[TH_YF8S][26-01-14 13:04:15][52] Exception in ThYf8s:
ian 14 11:04:15 teste sh[3742529]: Traceback (most recent call last):
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/base_serving_process.py", line 601, in run
ian 14 11:04:15 teste sh[3742529]:     _msg = self.__startup()
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/base_serving_process.py", line 713, in __startup
ian 14 11:04:15 teste sh[3742529]:     return self._startup()
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 959, in _startup
ian 14 11:04:15 teste sh[3742529]:     msg = self._setup_model()
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 347, in _setup_model
ian 14 11:04:15 teste sh[3742529]:     self.th_model = self._get_model(config=backend_model_map)
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/default_inference/th_yf_base.py", line 74, in _get_model
ian 14 11:04:15 teste sh[3742529]:     model, model_loaded_config, fn = self.prepare_model(
ian 14 11:04:15 teste sh[3742529]:   File "/usr/local/lib/python3.10/dist-packages/naeural_core/serving/base/basic_th.py", line 684, in prepare_model
ian 14 11:04:15 teste sh[3742529]:     raise RuntimeError("Could not prepare model: {}".format(last_error))
ian 14 11:04:15 teste sh[3742529]: RuntimeError: Could not prepare model: CUDA error: no kernel image is available for execution on the device
ian 14 11:04:15 teste sh[3742529]: CUDA kernel errors might be asynchronously reported at some other API call, so the stacktrace below might be incorrect.
ian 14 11:04:15 teste sh[3742529]: For debugging consider passing CUDA_LAUNCH_BLOCKING=1.
ian 14 11:04:15 teste sh[3742529]: Compile with `TORCH_USE_CUDA_DSA` to enable device-side assertions.
ian 14 11:04:15 teste sh[3742529]: