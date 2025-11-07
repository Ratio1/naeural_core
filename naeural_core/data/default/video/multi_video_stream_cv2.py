"""
Multi-source CV2 video data capture thread.

This implementation extends the single-stream CV2 DCT to operate on multiple
video sources defined through the `SOURCES` configuration key. It keeps the
per-stream behaviour identical to the single-stream version while:

* Handling reconnections asynchronously per stream so that healthy sources keep
  producing data even when others are reconnecting.
* Exposing metadata per captured frame that identifies the originating source.
* Feeding downstream consumers with batched inputs containing one entry per
  active stream.

The implementation mirrors the single-stream DCT as closely as possible so the
overall heuristics (AMD timing, crop/resize, notifications) behave the same for
each individual stream.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Dict, Iterable, List, Optional

import cv2
import numpy as np

from naeural_core import constants as ct
from naeural_core.data.base import DataCaptureThread
from naeural_core.data_structures import MetadataObject


@dataclass
class _StreamContext:
  name: str
  index: int
  config: Dict
  url: str
  metadata: MetadataObject = field(default_factory=MetadataObject)
  capture: Optional[cv2.VideoCapture] = None
  has_connection: bool = False
  reconnecting: bool = False
  last_url: Optional[str] = None
  nr_connection_issues: int = 0
  crt_frame: int = 0
  configured_size_error: bool = False
  resize_hw: Optional[tuple] = None
  frame_crop: Optional[List[int]] = None
  reconnect_thread: Optional[Thread] = field(default=None, repr=False)
  active: bool = True
  lock: Lock = field(default_factory=Lock, repr=False)


def _ensure_positive_randint_bounds(base_val: int) -> int:
  base_val = int(base_val)
  return max(1, base_val)


_CONFIG = {
  **DataCaptureThread.CONFIG,

  "MAX_RETRIES": 2,

  "AMD_TARGET_DPS": 0,
  "SIMULATE_AMD": False,

  "CAP_PROP_BUFFERSIZE": 0,

  "USE_FFMPEG": False,

  "CONFIGURED_H": -1,
  "CONFIGURED_W": -1,

  "SOURCES": [],

  "VALIDATION_RULES": {
    **DataCaptureThread.CONFIG["VALIDATION_RULES"],

    "AMD_TARGET_DPS": {
      "DESCRIPTION": (
        "For AMD architectures forces number of grabs per iteration based on "
        "the target required DPS."
      ),
      "TYPE": "int",
    },
    "SOURCES": {
      "DESCRIPTION": (
        "List of video source descriptors. Each entry must provide at least "
        "the `URL` key and can optionally override other capture parameters."
      ),
      "TYPE": "list",
    },
  },
}


class MultiVideoStreamCv2DataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._streams: Dict[str, _StreamContext] = {}
    self._streams_lock = Lock()
    self._metadata = MetadataObject(streams={})
    super(MultiVideoStreamCv2DataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    self._update_global_metadata()
    new_contexts = self._sync_streams()
    for ctx in new_contexts:
      self._launch_reconnect(ctx)
    return

  def _init(self):
    if not self.has_cap_resolution_config:
      str_err = "Cannot start MultiVideoStreamCv2DataCapture without CAP_RESOLUTION!"
      self.P(str_err, color="error")
      raise ValueError(str_err)
    return

  @property
  def is_hw_error(self):
    return any(ctx.configured_size_error for ctx in self._streams.values())

  def is_intel(self):
    if self.cfg_simulate_amd:
      result = False
    else:
      proc = self.log.get_processor_platform()
      if proc is not None:
        result = "intel" in proc.lower()
      else:
        result = False
    return result

  def _release(self):
    with self._streams_lock:
      for ctx in list(self._streams.values()):
        self._release_stream(ctx)
      self._streams.clear()
    return

  def _release_stream(self, stream: _StreamContext):
    stream.active = False
    with stream.lock:
      if stream.capture is not None:
        try:
          stream.capture.release()
        except Exception:
          pass
        stream.capture = None
      stream.has_connection = False
    stream.reconnecting = False
    stream.reconnect_thread = None
    return

  def _on_config_changed(self):
    new_contexts = self._sync_streams()
    for ctx in new_contexts:
      self._launch_reconnect(ctx)
    return

  def _sync_streams(self) -> List[_StreamContext]:
    sources = self.cfg_sources or []
    if not isinstance(sources, list):
      raise ValueError("SOURCES must be a list")

    normalized: List[tuple] = []
    used_names = set()
    for idx, raw_cfg in enumerate(sources):
      if isinstance(raw_cfg, str):
        cfg = {"URL": raw_cfg}
      elif isinstance(raw_cfg, dict):
        cfg = raw_cfg.copy()
      else:
        self.P(f"Ignoring invalid source definition at index {idx}: {raw_cfg}", color="r")
        continue

      url = cfg.get("URL")
      if not url:
        self.P(f"Ignoring source at index {idx} due to missing URL", color="r")
        continue

      proposed_name = cfg.get("NAME") or f"{self.cfg_name}_src_{idx}"
      name = proposed_name
      suffix = 1
      while name in used_names:
        name = f"{proposed_name}_{suffix}"
        suffix += 1
      used_names.add(name)
      normalized.append((name, idx, cfg, url))

    new_contexts: List[_StreamContext] = []
    with self._streams_lock:
      existing_names = set(self._streams.keys())
      normalized_names = {name for name, _, _, _ in normalized}

      removed_names = existing_names - normalized_names
      for name in removed_names:
        ctx = self._streams.pop(name)
        self.P(f"Removing video source '{name}'", color="y")
        self._release_stream(ctx)

      for name, idx, cfg, url in normalized:
        if name not in self._streams:
          ctx = _StreamContext(name=name, index=idx, config=cfg, url=url)
          ctx.metadata.update(stream_name=name, stream_index=idx, url=url)
          self._streams[name] = ctx
          new_contexts.append(ctx)
          self.P(f"Registered new video source '{name}' -> {url}", color="y")
        else:
          ctx = self._streams[name]
          ctx.index = idx
          ctx.config = cfg
          ctx.url = url
          ctx.metadata.update(stream_index=idx, url=url)
          if ctx.last_url is not None and ctx.last_url != url:
            self.P(
              f"[{name}] URL change detected from {ctx.last_url} to {url}, scheduling reconnect...",
              color="y",
            )
            self._mark_stream_disconnected(ctx, force_reconnect=True)

    self._update_global_metadata()
    return new_contexts

  def _maybe_reconnect(self):
    contexts = self._get_streams_snapshot()
    for ctx in contexts:
      if not ctx.active:
        continue

      if ctx.has_connection:
        with ctx.lock:
          capture = ctx.capture
          opened = capture is not None and capture.isOpened()
        if not opened:
          self.P(f"[{ctx.name}] Capture seems closed. Scheduling reconnect.", color="r")
          self._mark_stream_disconnected(ctx, force_reconnect=True)
        continue

      if not ctx.reconnecting:
        self._launch_reconnect(ctx)

    self._update_global_connection_flags()
    return

  def _launch_reconnect(self, stream: _StreamContext):
    if not stream.active:
      return
    if stream.reconnecting:
      return
    if self._stop:
      return

    stream.reconnecting = True
    thread_name = f"{ct.THREADS_PREFIX}reconn_{self.sanitize_name(stream.name)}"
    worker = Thread(target=self._reconnect_stream_worker, args=(stream,), name=thread_name, daemon=True)
    stream.reconnect_thread = worker
    worker.start()
    return

  def _reconnect_stream_worker(self, stream: _StreamContext):
    round_info = None
    while not self._stop and stream.active and not stream.has_connection:
      max_retries = self._get_stream_cfg_value(stream, "MAX_RETRIES", self.cfg_max_retries)
      if stream.nr_connection_issues == 0:
        msg = f"[{stream.name}] Connecting to url: {stream.url} (deq size: {self._deque.maxlen})"
        color = "g"
      else:
        msg = f"[{stream.name}] Reconnecting ({stream.nr_connection_issues}) to url: {stream.url}"
        color = "r"
      self.P(msg, color=color)

      nr_retry = 0
      round_info = None
      while nr_retry <= max_retries and not self._stop and stream.active and not stream.has_connection:
        self.sleep(1)
        nr_retry += 1
        stream.nr_connection_issues += 1
        try:
          self._release_stream_capture(stream)
          capture = self._open_stream(stream)
          if capture is None or not capture.isOpened():
            self.P(f"[{stream.name}]    Capture failed.", color="r")
            continue

          has_frame, frame = capture.read()
          if not has_frame or frame is None:
            self.P(f"[{stream.name}]    Capture read failed.", color="r")
            capture.release()
            continue

          skip_frames = self._get_stream_cfg_value(stream, "NR_SKIP_FRAMES", self.cfg_nr_skip_frames)
          if skip_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, skip_frames)

          try:
            raw_fps = int(capture.get(cv2.CAP_PROP_FPS))
          except Exception:
            raw_fps = 20
          fps = int(np.clip(raw_fps, 1, 120))
          buff_size = capture.get(cv2.CAP_PROP_BUFFERSIZE)
          height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
          width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
          frame_count = int(max(0, capture.get(cv2.CAP_PROP_FRAME_COUNT)))

          with stream.lock:
            stream.capture = capture
            stream.has_connection = True
            stream.last_url = stream.url
            stream.crt_frame = 0
            stream.configured_size_error = False
            stream.resize_hw = None
            stream.frame_crop = None
            stream.metadata.update(
              fps=fps,
              frame_h=height,
              frame_w=width,
              frame_count=frame_count,
              buffersize=buff_size,
              stream_name=stream.name,
              stream_index=stream.index,
              url=stream.url,
            )

          self._configure_stream_resize_crop(stream, height, width)
          self.reset_received_first_data()
          self.P(f"[{stream.name}] Connection seems to be established.")
          self._create_stream_notification(stream, success=True, info=None)
          self._update_global_connection_flags()
          self._update_global_metadata()
          stream.reconnecting = False
          return
        except Exception:
          round_info = traceback.format_exc()
          self.P(f"[{stream.name}] `_maybe_reconnect` exception: {round_info}", color="r")
      # end while retries

      sleep_base = _ensure_positive_randint_bounds(self.cfg_sleep_time_on_error)
      sleep_high = max(sleep_base + 1, sleep_base * 3 + 1)
      sleep_time = int(np.random.randint(sleep_base, sleep_high))

      self._create_stream_notification(
        stream,
        success=False,
        info=round_info,
        extra={
          "attempts": nr_retry,
          "sleep_time": sleep_time,
        },
      )

      if not self.is_reconnectable or not stream.active:
        break

      self.sleep(sleep_time)

    stream.reconnecting = False
    self._update_global_connection_flags()
    return

  def _create_stream_notification(self, stream: _StreamContext, success: bool, info=None, extra=None):
    extra = extra or {}
    metadata_snapshot = self.deepcopy(stream.metadata.__dict__)
    metadata_snapshot["has_connection"] = stream.has_connection
    metadata_snapshot["nr_connection_issues"] = stream.nr_connection_issues

    if success:
      msg = (
        f"Video DCT '{self.cfg_name}' successfully connected stream '{stream.name}'. "
        f"Overall {stream.nr_connection_issues} reconnects."
      )
      notification_type = ct.STATUS_TYPE.STATUS_NORMAL
      color = "g"
    else:
      attempts = extra.get("attempts", 0)
      sleep_time = extra.get("sleep_time", 0)
      msg = (
        f"Abnormal functioning of Video DCT '{self.cfg_name}' stream '{stream.name}' "
        f"failed after {attempts} retries (overall {stream.nr_connection_issues} reconnects). "
        f"Sleeping stream reconnect for {sleep_time:.1f}s"
      )
      notification_type = ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING
      color = "e"

    self.P(msg, color=color)
    self._create_notification(
      notif=notification_type,
      msg=msg,
      info=info,
      video_stream_info=metadata_snapshot,
      stream_name=stream.name,
      displayed=True,
    )
    return

  def _open_stream(self, stream: _StreamContext):
    use_ffmpeg = bool(self._get_stream_cfg_value(stream, "USE_FFMPEG", self.cfg_use_ffmpeg))
    if use_ffmpeg:
      self.P(f"[{stream.name}] Opening capture with CAP_FFMPEG...")
      cap = cv2.VideoCapture(stream.url, cv2.CAP_FFMPEG)
    else:
      cap = cv2.VideoCapture(stream.url)

    cap_prop_buff = self._get_stream_cfg_value(stream, "CAP_PROP_BUFFERSIZE", self.cfg_cap_prop_buffersize)
    if cap_prop_buff and cap_prop_buff > 0:
      cap.set(cv2.CAP_PROP_BUFFERSIZE, cap_prop_buff)
    return cap

  def _get_stream_cfg_value(self, stream: _StreamContext, key: str, default=None):
    if key in stream.config and stream.config[key] is not None:
      return stream.config[key]
    attr_name = f"cfg_{key.lower()}"
    if hasattr(self, attr_name):
      return getattr(self, attr_name)
    return self.config.get(key, default)

  def _get_streams_snapshot(self) -> Iterable[_StreamContext]:
    with self._streams_lock:
      return list(self._streams.values())

  def _run_data_aquisition_step(self):
    inputs = []
    for stream in self._get_streams_snapshot():
      if not stream.active or not stream.has_connection:
        continue

      with stream.lock:
        capture = stream.capture
      if capture is None or not capture.isOpened():
        self.P(f"[{stream.name}] Capture closed mid-loop. Scheduling reconnect.", color="r")
        self._mark_stream_disconnected(stream, force_reconnect=True)
        continue

      has_frame, frame = self._capture_read(stream, capture)
      if not has_frame or frame is None:
        self.P(f"[{stream.name}] Failed to read frame. Scheduling reconnect.", color="r")
        self._mark_stream_disconnected(stream, force_reconnect=True)
        continue

      frame = self._maybe_resize_crop_stream(stream, frame)
      frame = np.ascontiguousarray(frame[:, :, ::-1])

      self._verify_configured_size(stream, frame)

      stream.crt_frame += 1
      stream.metadata.frame_current = stream.crt_frame

      inputs.append(self._new_input(img=frame, metadata=self._build_frame_metadata(stream)))

    if inputs:
      self._add_inputs(inputs)
      self.has_connection = True
    elif not any(ctx.has_connection for ctx in self._streams.values()):
      self.has_connection = False
    return

  def _verify_configured_size(self, stream: _StreamContext, frame):
    configured_h = self._get_stream_cfg_value(stream, "CONFIGURED_H", self.cfg_configured_h)
    configured_w = self._get_stream_cfg_value(stream, "CONFIGURED_W", self.cfg_configured_w)

    mismatch_h = configured_h > 0 and configured_h != frame.shape[0]
    mismatch_w = configured_w > 0 and configured_w != frame.shape[1]
    if (mismatch_h or mismatch_w) and not stream.configured_size_error:
      stream.configured_size_error = True
      msg = (
        f"ERROR: Stream '{stream.name}' configured at {configured_h}x{configured_w} "
        f"decoded at {frame.shape[0]}x{frame.shape[1]}"
      )
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
        stream_name=stream.name,
        msg=msg,
      )
    return

  def _build_frame_metadata(self, stream: _StreamContext):
    data = self.deepcopy(stream.metadata.__dict__)
    data.update(
      source_name=stream.name,
      source_index=stream.index,
      source_url=stream.url,
      connected=stream.has_connection,
    )
    return data

  def _maybe_resize_crop_stream(self, stream: _StreamContext, img):
    if stream.resize_hw is not None:
      target_h, target_w = stream.resize_hw
      self.start_timer(self._stream_timer_id(stream, "cv2_resize"))
      img = cv2.resize(img, dsize=(target_w, target_h))
      self.end_timer(self._stream_timer_id(stream, "cv2_resize"))
    elif stream.frame_crop is not None:
      self.start_timer(self._stream_timer_id(stream, "cv2_crop"))
      top, left, bottom, right = stream.frame_crop
      img = img[top:bottom, left:right]
      self.end_timer(self._stream_timer_id(stream, "cv2_crop"))
    return img

  def _configure_stream_resize_crop(self, stream: _StreamContext, height: int, width: int):
    stream.resize_hw = None
    stream.frame_crop = None

    metadata_cfg = {}
    base_cfg = self.cfg_stream_config_metadata or {}
    if isinstance(base_cfg, dict):
      metadata_cfg.update(base_cfg)
    stream_cfg = stream.config.get("STREAM_CONFIG_METADATA", {}) or {}
    if isinstance(stream_cfg, dict):
      metadata_cfg.update(stream_cfg)

    resize_h = metadata_cfg.get(ct.RESIZE_H)
    resize_w = metadata_cfg.get(ct.RESIZE_W)
    if resize_h is not None and resize_h > 0:
      ratio = height / width if width else 1
      resize_w = resize_w if resize_w is not None else int(resize_h / ratio)
      stream.resize_hw = (int(resize_h), int(resize_w))
      self.P(
        f"[{stream.name}] Live resize enabled from {(height, width)} to {stream.resize_hw}",
        color="y",
      )
      stream.metadata.resize_hw = stream.resize_hw

    frame_crop = metadata_cfg.get(ct.FRAME_CROP, metadata_cfg.get(ct.CAPTURE_CROP))
    if frame_crop is not None and stream.resize_hw is None:
      if (
        isinstance(frame_crop, list)
        and len(frame_crop) == 4
        and frame_crop[0] <= frame_crop[2]
        and frame_crop[1] <= frame_crop[3]
      ):
        stream.frame_crop = frame_crop
        cropped_h = frame_crop[2] - frame_crop[0]
        cropped_w = frame_crop[3] - frame_crop[1]
        stream.metadata.frame_h = cropped_h
        stream.metadata.frame_w = cropped_w
        self.P(
          f"[{stream.name}] Enabling cropping from {(height, width)} to {(cropped_h, cropped_w)} "
          f"(TLBR:{frame_crop})",
          color="y",
        )
      else:
        self.P(
          f"[{stream.name}] Invalid FRAME_CROP {frame_crop}. Using original frame size.",
          color="r",
        )
    return

  def _capture_read(self, stream: _StreamContext, capture):
    has_frame, frame = False, None
    timer_suffix = self._stream_timer_id(stream, "cv2")

    if ((self.cap_resolution >= self._get_stream_fps_max_thr(stream) and self.cfg_amd_target_dps == 0)
        or self.is_intel()):
      self.start_timer(f"{timer_suffix}_read")
      self.start_timer(f"{timer_suffix}_read_dps{self.cap_resolution}")
      has_frame, frame = capture.read()
      self.end_timer(f"{timer_suffix}_read_dps{self.cap_resolution}")
      self.end_timer(f"{timer_suffix}_read")
    else:
      nr_grabs = self._get_nr_grabs(stream)
      self.start_timer(f"{timer_suffix}_grab_retrv_dps{self.cap_resolution}")
      self.start_timer(f"{timer_suffix}_grab_x{nr_grabs}")
      for _ in range(nr_grabs):
        self.start_timer(f"{timer_suffix}_grab")
        try:
          _ = capture.grab()
        except Exception:
          self.end_timer(f"{timer_suffix}_grab")
          break
        self.end_timer(f"{timer_suffix}_grab")
      self.end_timer(f"{timer_suffix}_grab_x{nr_grabs}")
      self.start_timer(f"{timer_suffix}_retrieve")
      has_frame, frame = capture.retrieve()
      self.end_timer(f"{timer_suffix}_retrieve")
      self.end_timer(f"{timer_suffix}_grab_retrv_dps{self.cap_resolution}")

    if not self.is_intel() and self.cfg_amd_target_dps == 0:
      self._recalc_cap_resolution(stream)
    return has_frame, frame

  def _stream_timer_id(self, stream: _StreamContext, base: str) -> str:
    suffix = self.sanitize_name(stream.name)
    return f"{base}__{suffix}"

  def _get_stream_fps_max_thr(self, stream: _StreamContext):
    fps = stream.metadata.__dict__.get("fps")
    if fps is None:
      return float("inf")
    return fps * 0.6

  def _get_nr_grabs(self, stream: _StreamContext):
    if self.cfg_amd_target_dps > 0:
      n_grabs = int(np.ceil(self.cfg_cap_resolution / self.cfg_amd_target_dps))
    else:
      fps = stream.metadata.__dict__.get("fps", 1)
      n_grabs = int(np.ceil(fps / self.cap_resolution)) + 1
    return n_grabs

  def _recalc_cap_resolution(self, stream: _StreamContext):
    fps = stream.metadata.__dict__.get("fps")
    if fps is None:
      return
    WARM_UP_SEC = 60
    MAX_TIME = 2
    if self.cap_resolution >= fps * 0.6:
      nr_streams = self.get_nr_parallel_captures() + len(self._streams)
      timer_id = f"{self._stream_timer_id(stream, 'cv2')}_read"
      read_stats = self.get_timer(timer_id)
      if read_stats is None:
        return
      read_time = read_stats.get("MEAN", 0)
      read_count = read_stats.get("COUNT", 0)
      if read_count > (self.cap_resolution * WARM_UP_SEC):
        total_read_time = read_time * self.cap_resolution * nr_streams
        if total_read_time >= MAX_TIME:
          configured_cap_res = self.get_cap_or_forced_resolution()
          self._heuristic_cap_resolution = round(MAX_TIME / (read_time * nr_streams), 1)
          self.P(
            "Total cv2r time @{} load: {:.4f}s({:.4f}s/strm/itr) exceding {}s. "
            "Forcing cap {} @ {:.1f} dps, nr_grabs={} (stream fps: {} cap dps:{}/{})".format(
              nr_streams,
              total_read_time,
              read_time,
              MAX_TIME,
              self.cfg_name,
              self._heuristic_cap_resolution,
              self._get_nr_grabs(stream),
              stream.metadata.__dict__.get("fps"),
              self.cap_resolution,
              configured_cap_res,
            ),
            color="m",
          )
    return

  def _release_stream_capture(self, stream: _StreamContext):
    with stream.lock:
      if stream.capture is not None:
        try:
          stream.capture.release()
        except Exception:
          pass
        stream.capture = None
      stream.has_connection = False
    return

  def _mark_stream_disconnected(self, stream: _StreamContext, force_reconnect: bool):
    self._release_stream_capture(stream)
    if not self.is_reconnectable:
      stream.active = False
    if force_reconnect and self.is_reconnectable:
      self._launch_reconnect(stream)
    self._update_global_connection_flags()
    return

  def _update_global_metadata(self):
    streams_snapshot = {
      name: self.deepcopy(ctx.metadata.__dict__)
      for name, ctx in self._streams.items()
    }
    active_names = [name for name, ctx in self._streams.items() if ctx.has_connection]
    self._metadata.update(streams=streams_snapshot, connected_streams=active_names)
    self._stream_metadata.streams = streams_snapshot
    self._stream_metadata.connected_streams = active_names
    return

  def _update_global_connection_flags(self):
    total_issues = sum(ctx.nr_connection_issues for ctx in self._streams.values())
    self.nr_connection_issues = total_issues
    self.has_connection = any(ctx.has_connection for ctx in self._streams.values())
    return
