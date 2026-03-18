#!/usr/bin/env python3
"""
Passive Ratio1 SDK bandwidth capture for mainnet.

Primary metric: raw MQTT payload bytes as delivered to the SDK communicator.
Secondary metric: decoded heartbeat body composition after ENCODED_DATA
decompression, reported separately from raw bandwidth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import sleep, time
from typing import Any

from ratio1.base.generic_session import GenericSession
from ratio1.comm.mqtt_wrapper import MQTTWrapper
from ratio1.const import comms as comm_ct
from ratio1.default.session.mqtt_session import MqttSession


LARGE_FIELD_THRESHOLD = 1024
MAX_TOP_ROWS = 20
DEFAULT_OUTPUT_DIR = "xperimental/payloads_tests/evidence/raw_bandwidth"


def utc_now_iso() -> str:
  return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_json_text(value: Any) -> str:
  return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def compact_json_bytes(value: Any) -> int:
  return len(compact_json_text(value).encode("utf-8"))


def safe_text_size(value: str | None) -> int:
  if value is None:
    return 0
  return len(value.encode("utf-8"))


def human_bytes(num_bytes: float) -> str:
  value = float(num_bytes)
  for unit in ["B", "KB", "MB", "GB", "TB"]:
    if value < 1024.0 or unit == "TB":
      if unit == "B":
        return f"{int(value)}{unit}"
      return f"{value:.1f}{unit}"
    value /= 1024.0
  return f"{value:.1f}TB"


def pct(part: float, whole: float) -> float:
  if whole <= 0:
    return 0.0
  return round(100.0 * part / whole, 1)


def percentile(values: list[int], q: float) -> int:
  if not values:
    return 0
  if len(values) == 1:
    return values[0]
  ordered = sorted(values)
  idx = int(math.ceil((q / 100.0) * len(ordered))) - 1
  idx = max(0, min(idx, len(ordered) - 1))
  return ordered[idx]


def compact_preview(value: Any, max_str: int = 80) -> str:
  if isinstance(value, dict):
    return f"<dict {len(value)} keys, {compact_json_bytes(value)}B>"
  if isinstance(value, list):
    return f"<list {len(value)} items, {compact_json_bytes(value)}B>"
  if isinstance(value, str):
    trimmed = value[:max_str]
    if len(value) > max_str:
      trimmed += "..."
    return f"<string {len(value.encode('utf-8'))}B> {trimmed}"
  return repr(value)


def normalize_path(value: Any) -> list[Any]:
  if isinstance(value, list):
    result = list(value[:4])
    while len(result) < 4:
      result.append(None)
    return result
  return [None, None, None, None]


def render_sender_label(sender_id: str | None, sender_addr: str | None) -> str | None:
  if sender_id and sender_addr:
    return f"{sender_id}<{sender_addr}>"
  return sender_id or sender_addr


def message_class(event_type: str | None, signature: str | None, raw_obj: dict[str, Any]) -> str:
  ev = (event_type or "").upper()
  if ev == "HEARTBEAT":
    return "heartbeat"
  if ev == "NOTIFICATION":
    notif_type = raw_obj.get("NOTIFICATION_TYPE") or raw_obj.get("STATUS_TYPE") or "-"
    return f"notification:{notif_type}"
  if ev == "PAYLOAD":
    return f"payload:{signature or '-'}"
  if signature:
    return f"payload:{signature}"
  return ev.lower() or "unknown"


def count_prefixed_fields(obj: dict[str, Any], prefix: str) -> int:
  return sum(1 for key in obj if isinstance(key, str) and key.startswith(prefix))


def collect_presence(obj: dict[str, Any]) -> dict[str, bool]:
  keys = set(obj.keys())
  return {
    "IMG": "IMG" in keys,
    "IMG_ORIG": "IMG_ORIG" in keys,
    "HISTORY": "HISTORY" in keys,
    "DCT_STATS": "DCT_STATS" in keys,
    "COMM_STATS": "COMM_STATS" in keys,
    "ACTIVE_PLUGINS": "ACTIVE_PLUGINS" in keys,
    "CONFIG_STREAMS": "CONFIG_STREAMS" in keys,
    "EE_WHITELIST": "EE_WHITELIST" in keys,
    "TAGS": "TAGS" in keys,
    "ID_TAGS": "ID_TAGS" in keys,
  }


def default_like(value: Any) -> bool:
  return value in (None, "", [], {}, False)


def summarize_raw_object(raw_obj: dict[str, Any], raw_bytes: int, recv_ts: float, comm_type: str, topic: str, log) -> dict[str, Any]:
  path = normalize_path(raw_obj.get("EE_PAYLOAD_PATH"))
  node, stream, signature, instance = path
  event_type = raw_obj.get("EE_EVENT_TYPE")
  sender_addr = raw_obj.get("EE_SENDER")
  sender_id = raw_obj.get("EE_ID")
  field_sizes = {key: compact_json_bytes(value) for key, value in raw_obj.items()}
  large_fields = sorted(
    [{"field": key, "bytes": size} for key, size in field_sizes.items() if size >= LARGE_FIELD_THRESHOLD],
    key=lambda item: item["bytes"],
    reverse=True,
  )
  preview_fields = {}
  for item in large_fields[:5]:
    preview_fields[item["field"]] = compact_preview(raw_obj.get(item["field"]))

  shape_key = "|".join(sorted(str(key) for key in raw_obj.keys()))
  row = {
    "recv_ts": recv_ts,
    "recv_iso": datetime.fromtimestamp(recv_ts, tz=timezone.utc).isoformat(timespec="seconds"),
    "comm_type": comm_type,
    "topic": topic,
    "raw_bytes": raw_bytes,
    "sender": render_sender_label(sender_id, sender_addr),
    "sender_addr": sender_addr,
    "sender_id": sender_id,
    "destination": raw_obj.get("EE_DESTINATION") or raw_obj.get("EE_DEST"),
    "event_type": event_type,
    "stream": stream,
    "signature": signature,
    "instance": instance,
    "message_class": message_class(event_type, signature, raw_obj),
    "encrypted": bool(raw_obj.get("EE_IS_ENCRYPTED", False)),
    "top_level_keys": sorted(str(key) for key in raw_obj.keys()),
    "top_level_key_count": len(raw_obj),
    "raw_field_sizes": field_sizes,
    "large_raw_fields": large_fields,
    "raw_preview": preview_fields,
    "_c_field_count": count_prefixed_fields(raw_obj, "_C_"),
    "_p_field_count": count_prefixed_fields(raw_obj, "_P_"),
    "presence": collect_presence(raw_obj),
    "empty_or_default_fields": sorted(key for key, value in raw_obj.items() if default_like(value)),
    "shape_hash": hashlib.sha1(shape_key.encode("utf-8")).hexdigest()[:10],
  }

  if row["message_class"] == "heartbeat":
    hb_version = raw_obj.get("HEARTBEAT_VERSION")
    hb_version_norm = str(hb_version).lower() if hb_version is not None else None
    encoded_data = raw_obj.get("ENCODED_DATA")
    row["heartbeat_version"] = hb_version
    row["heartbeat_encoded_text_bytes"] = safe_text_size(encoded_data) if isinstance(encoded_data, str) else 0
    row["heartbeat_decoded_bytes"] = 0
    row["heartbeat_inner_field_sizes"] = {}
    row["heartbeat_inner_large_fields"] = []
    if hb_version_norm == "v2" and isinstance(encoded_data, str):
      try:
        hb_inner = json.loads(log.decompress_text(encoded_data))
        inner_field_sizes = {key: compact_json_bytes(value) for key, value in hb_inner.items()}
        row["heartbeat_decoded_bytes"] = compact_json_bytes(hb_inner)
        row["heartbeat_inner_field_sizes"] = inner_field_sizes
        row["heartbeat_inner_large_fields"] = sorted(
          [{"field": key, "bytes": size} for key, size in inner_field_sizes.items() if size >= LARGE_FIELD_THRESHOLD],
          key=lambda item: item["bytes"],
          reverse=True,
        )
      except Exception as exc:
        row["heartbeat_decode_error"] = str(exc)

  return row


@dataclass
class CaptureArtifacts:
  capture_file: Path
  summary_json: Path
  results_md: Path
  metadata_json: Path


class JsonlWriter:
  def __init__(self, path: Path):
    self.path = path
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self._handle = self.path.open("w", encoding="utf-8")
    self._lock = Lock()

  def write(self, row: dict[str, Any]) -> None:
    with self._lock:
      self._handle.write(json.dumps(row, ensure_ascii=True) + "\n")
      self._handle.flush()

  def close(self) -> None:
    with self._lock:
      self._handle.close()


class RecordingMQTTWrapper(MQTTWrapper):
  def __init__(self, *args, record_callback=None, **kwargs):
    self._record_callback = record_callback
    super().__init__(*args, **kwargs)

  def _callback_on_message(self, client, userdata, message, *args, **kwargs):
    if self._record_callback is not None:
      try:
        self._record_callback(
          comm_type=self._comm_type,
          topic=message.topic,
          payload_bytes=bytes(message.payload),
          log=self.log,
        )
      except Exception:
        pass
    return super()._callback_on_message(client, userdata, message, *args, **kwargs)


class PassiveBandwidthMqttSession(MqttSession):
  def __init__(self, *args, raw_record_callback=None, **kwargs):
    self._raw_record_callback = raw_record_callback
    super().__init__(*args, **kwargs)

  def _make_wrapper(self, *, send_channel_name=None, recv_channel_name=None, comm_type=None, recv_buff=None):
    return RecordingMQTTWrapper(
      log=self.log,
      config=self._config,
      send_channel_name=send_channel_name,
      recv_channel_name=recv_channel_name,
      comm_type=comm_type,
      recv_buff=recv_buff,
      connection_name=self.name,
      verbosity=self._verbosity,
      record_callback=self._raw_record_callback,
    )

  def startup(self):
    self._default_communicator = self._make_wrapper(
      send_channel_name=comm_ct.COMMUNICATION_PAYLOADS_CHANNEL,
      recv_channel_name=comm_ct.COMMUNICATION_PAYLOADS_CHANNEL,
      comm_type=comm_ct.COMMUNICATION_DEFAULT,
      recv_buff=self._payload_messages,
    )
    self._heartbeats_communicator = self._make_wrapper(
      send_channel_name=comm_ct.COMMUNICATION_CONFIG_CHANNEL,
      recv_channel_name=comm_ct.COMMUNICATION_CTRL_CHANNEL,
      comm_type=comm_ct.COMMUNICATION_HEARTBEATS,
      recv_buff=self._hb_messages,
    )
    self._notifications_communicator = self._make_wrapper(
      recv_channel_name=comm_ct.COMMUNICATION_NOTIF_CHANNEL,
      comm_type=comm_ct.COMMUNICATION_NOTIFICATIONS,
      recv_buff=self._notif_messages,
    )
    self._MqttSession__communicators = {
      "default": self._default_communicator,
      "heartbeats": self._heartbeats_communicator,
      "notifications": self._notifications_communicator,
    }
    return GenericSession.startup(self)

  def _GenericSession__request_pipelines_from_net_config_monitor(self, node_addr=None):
    if not hasattr(self, "_passive_netconfig_warning_shown"):
      self._passive_netconfig_warning_shown = True
      self.P("Passive bandwidth capture: disabling automatic net-config requests.", color="y")
    return


class CaptureRecorder:
  def __init__(self, jsonl_path: Path):
    self.writer = JsonlWriter(jsonl_path)
    self.lock = Lock()
    self.count = 0
    self.first_recv_ts = None
    self.last_recv_ts = None

  def record(self, *, comm_type: str, topic: str, payload_bytes: bytes, log) -> None:
    recv_ts = time()
    raw_bytes = len(payload_bytes)
    try:
      raw_text = payload_bytes.decode("utf-8")
      raw_obj = json.loads(raw_text)
    except Exception as exc:
      row = {
        "recv_ts": recv_ts,
        "recv_iso": datetime.fromtimestamp(recv_ts, tz=timezone.utc).isoformat(timespec="seconds"),
        "comm_type": comm_type,
        "topic": topic,
        "raw_bytes": raw_bytes,
        "parse_error": str(exc),
      }
    else:
      row = summarize_raw_object(
        raw_obj=raw_obj,
        raw_bytes=raw_bytes,
        recv_ts=recv_ts,
        comm_type=comm_type,
        topic=topic,
        log=log,
      )
    self.writer.write(row)
    with self.lock:
      self.count += 1
      if self.first_recv_ts is None:
        self.first_recv_ts = recv_ts
      self.last_recv_ts = recv_ts

  def close(self) -> None:
    self.writer.close()


def top_n_rows(counter_like: dict[str, dict[str, Any]], sort_key: str, limit: int = MAX_TOP_ROWS) -> list[dict[str, Any]]:
  rows = list(counter_like.values())
  rows.sort(key=lambda item: item.get(sort_key, 0), reverse=True)
  return rows[:limit]


def update_field_totals(total_sizes: dict[str, int], total_counts: dict[str, int], field_sizes: dict[str, int]) -> None:
  for field, size in field_sizes.items():
    total_sizes[field] += size
    total_counts[field] += 1


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
  lines = [
    "| " + " | ".join(headers) + " |",
    "| " + " | ".join(["---"] * len(headers)) + " |",
  ]
  for row in rows:
    lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
  return "\n".join(lines)


def analyze_capture(capture_file: Path, metadata_path: Path, results_md: Path, summary_json: Path, args: argparse.Namespace) -> dict[str, Any]:
  rows = []
  with capture_file.open("r", encoding="utf-8") as handle:
    for line in handle:
      rows.append(json.loads(line))

  metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
  duration = float(metadata["duration_seconds"])
  if duration <= 0 and rows:
    duration = max(0.001, rows[-1]["recv_ts"] - rows[0]["recv_ts"])
  total_raw_bytes = sum(row.get("raw_bytes", 0) for row in rows)
  total_messages = len(rows)
  bytes_per_second = total_raw_bytes / duration if duration > 0 else 0.0

  by_class = {}
  by_sender = {}
  by_stream_sig = {}
  raw_field_sizes = defaultdict(int)
  raw_field_counts = defaultdict(int)
  hb_inner_field_sizes = defaultdict(int)
  hb_inner_field_counts = defaultdict(int)
  empty_field_sizes = defaultdict(int)
  shape_counter = Counter()
  first_half_bytes = 0
  second_half_bytes = 0
  first_half_msgs = 0
  second_half_msgs = 0
  minute_bins = defaultdict(lambda: defaultdict(int))
  heartbeat_decoded_total = 0

  if rows:
    start_ts = rows[0]["recv_ts"]
    end_ts = rows[-1]["recv_ts"]
  else:
    start_ts = metadata["start_ts"]
    end_ts = metadata["end_ts"]
  midpoint = start_ts + ((end_ts - start_ts) / 2.0 if end_ts >= start_ts else 0.0)

  for row in rows:
    raw_bytes = row.get("raw_bytes", 0)
    msg_class = row.get("message_class", "unknown")
    sender = row.get("sender") or "unknown"
    stream = row.get("stream") or "-"
    signature = row.get("signature") or "-"
    recv_ts = row.get("recv_ts", start_ts)

    by_class.setdefault(msg_class, {"message_class": msg_class, "count": 0, "total_bytes": 0, "sizes": []})
    by_class[msg_class]["count"] += 1
    by_class[msg_class]["total_bytes"] += raw_bytes
    by_class[msg_class]["sizes"].append(raw_bytes)

    by_sender.setdefault(sender, {"sender": sender, "count": 0, "total_bytes": 0})
    by_sender[sender]["count"] += 1
    by_sender[sender]["total_bytes"] += raw_bytes

    stream_sig_key = f"{stream} / {signature}"
    by_stream_sig.setdefault(stream_sig_key, {"stream_signature": stream_sig_key, "count": 0, "total_bytes": 0})
    by_stream_sig[stream_sig_key]["count"] += 1
    by_stream_sig[stream_sig_key]["total_bytes"] += raw_bytes

    update_field_totals(raw_field_sizes, raw_field_counts, row.get("raw_field_sizes", {}))
    update_field_totals(hb_inner_field_sizes, hb_inner_field_counts, row.get("heartbeat_inner_field_sizes", {}))

    for field in row.get("empty_or_default_fields", []):
      size = row.get("raw_field_sizes", {}).get(field, 0)
      empty_field_sizes[field] += size

    heartbeat_decoded_total += row.get("heartbeat_decoded_bytes", 0)
    shape_counter[row.get("shape_hash", "missing")] += 1

    minute_idx = int((recv_ts - start_ts) // 60) if rows else 0
    minute_bins[minute_idx][msg_class] += raw_bytes

    if recv_ts <= midpoint:
      first_half_msgs += 1
      first_half_bytes += raw_bytes
    else:
      second_half_msgs += 1
      second_half_bytes += raw_bytes

  by_class_rows = []
  for item in by_class.values():
    sizes = item.pop("sizes")
    item["avg_bytes"] = round(item["total_bytes"] / item["count"], 1) if item["count"] else 0
    item["p95_bytes"] = percentile(sizes, 95)
    item["max_bytes"] = max(sizes) if sizes else 0
    by_class_rows.append(item)
  by_class_rows.sort(key=lambda item: item["total_bytes"], reverse=True)

  top_raw_fields = sorted(
    [
      {
        "field": field,
        "messages_present": raw_field_counts[field],
        "total_bytes": size,
        "avg_when_present": round(size / raw_field_counts[field], 1) if raw_field_counts[field] else 0,
      }
      for field, size in raw_field_sizes.items()
    ],
    key=lambda item: item["total_bytes"],
    reverse=True,
  )

  top_hb_inner_fields = sorted(
    [
      {
        "field": field,
        "messages_present": hb_inner_field_counts[field],
        "total_bytes": size,
        "avg_when_present": round(size / hb_inner_field_counts[field], 1) if hb_inner_field_counts[field] else 0,
      }
      for field, size in hb_inner_field_sizes.items()
    ],
    key=lambda item: item["total_bytes"],
    reverse=True,
  )

  top_empty_fields = sorted(
    [{"field": field, "total_bytes": size} for field, size in empty_field_sizes.items()],
    key=lambda item: item["total_bytes"],
    reverse=True,
  )

  largest_rows = sorted(rows, key=lambda row: row.get("raw_bytes", 0), reverse=True)[:5]
  top_shapes = shape_counter.most_common(5)

  results = {
    "scope": {
      "script_path": "xperimental/payloads_tests/sdk_bandwidth_capture.py",
      "capture_file": str(capture_file),
      "summary_json": str(summary_json),
      "results_md": str(results_md),
      "network": args.network,
      "duration_seconds": duration,
      "message_count": total_messages,
      "stop_reason": metadata["stop_reason"],
    },
    "totals": {
      "raw_bytes": total_raw_bytes,
      "bytes_per_second": bytes_per_second,
      "bytes_per_minute": bytes_per_second * 60.0,
      "heartbeat_decoded_total": heartbeat_decoded_total,
      "first_half_messages": first_half_msgs,
      "first_half_bytes": first_half_bytes,
      "second_half_messages": second_half_msgs,
      "second_half_bytes": second_half_bytes,
    },
    "by_class": by_class_rows,
    "by_sender": top_n_rows(by_sender, "total_bytes"),
    "by_stream_signature": top_n_rows(by_stream_sig, "total_bytes"),
    "top_raw_fields": top_raw_fields[:MAX_TOP_ROWS],
    "top_heartbeat_inner_fields": top_hb_inner_fields[:MAX_TOP_ROWS],
    "top_empty_fields": top_empty_fields[:MAX_TOP_ROWS],
    "largest_rows": largest_rows,
    "top_shapes": top_shapes,
    "minute_bins": {str(idx): values for idx, values in sorted(minute_bins.items())},
  }

  summary_json.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")

  msg_class_table = render_table(
    ["message class", "count", "total raw bytes", "avg bytes", "p95 bytes", "max bytes", "byte share"],
    [
      [
        item["message_class"],
        item["count"],
        human_bytes(item["total_bytes"]),
        int(item["avg_bytes"]),
        item["p95_bytes"],
        item["max_bytes"],
        f"{pct(item['total_bytes'], total_raw_bytes)}%",
      ]
      for item in by_class_rows[:MAX_TOP_ROWS]
    ],
  )

  sender_table = render_table(
    ["sender", "count", "total raw bytes", "avg bytes"],
    [
      [
        item["sender"],
        item["count"],
        human_bytes(item["total_bytes"]),
        int(item["total_bytes"] / item["count"]) if item["count"] else 0,
      ]
      for item in top_n_rows(by_sender, "total_bytes")[:10]
    ],
  )

  stream_table = render_table(
    ["stream / signature", "count", "total raw bytes", "avg bytes"],
    [
      [
        item["stream_signature"],
        item["count"],
        human_bytes(item["total_bytes"]),
        int(item["total_bytes"] / item["count"]) if item["count"] else 0,
      ]
      for item in top_n_rows(by_stream_sig, "total_bytes")[:10]
    ],
  )

  raw_field_table = render_table(
    ["field", "messages present", "total estimated raw bytes", "avg bytes when present"],
    [
      [
        item["field"],
        item["messages_present"],
        human_bytes(item["total_bytes"]),
        int(item["avg_when_present"]),
      ]
      for item in top_raw_fields[:15]
    ],
  )

  hb_inner_field_table = render_table(
    ["field", "messages present", "total decoded bytes", "avg bytes when present"],
    [
      [
        item["field"],
        item["messages_present"],
        human_bytes(item["total_bytes"]),
        int(item["avg_when_present"]),
      ]
      for item in top_hb_inner_fields[:15]
    ],
  )

  largest_examples = []
  for idx, row in enumerate(largest_rows, start=1):
    largest_examples.append(
      f"- Example {idx}: `{row.get('message_class')}` from `{row.get('sender')}` "
      f"({human_bytes(row.get('raw_bytes', 0))}, stream=`{row.get('stream')}`, signature=`{row.get('signature')}`)\n"
      f"  large raw fields: " +
      ", ".join(f"{item['field']}={human_bytes(item['bytes'])}" for item in row.get("large_raw_fields", [])[:5]) + "\n"
      f"  preview: `{json.dumps(row.get('raw_preview', {}), ensure_ascii=True)}`"
    )

  minute_lines = []
  for minute_idx, values in sorted(minute_bins.items()):
    top_class = sorted(values.items(), key=lambda item: item[1], reverse=True)[:3]
    rendered = ", ".join(f"{name}={human_bytes(size)}" for name, size in top_class)
    minute_lines.append(f"- minute {minute_idx}: {rendered}")

  md = f"""# Ratio1 SDK Raw Bandwidth Capture Results

## Scope
- script path: `xperimental/payloads_tests/sdk_bandwidth_capture.py`
- capture command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds {args.seconds} --max-messages {args.max_messages}`
- analysis command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --analyze-only --capture-file {capture_file}`
- network: `{args.network}`
- capture window: `{metadata['start_iso']}` to `{metadata['end_iso']}` ({duration:.1f}s)
- message count: `{total_messages}`
- stop reason: `{metadata['stop_reason']}`

## Measurement Rules
- Primary bandwidth metric is raw MQTT payload size: `len(message.payload)` captured before SDK parsing or heartbeat decompression.
- Heartbeat decoded-body analysis is reported separately and is **not** counted as raw bandwidth.
- MQTT topic/header overhead is still excluded because the SDK callback surface does not expose it.
- The session disables automatic SDK net-config requests to keep the run passive.

## Executive Summary
- Raw MQTT payload throughput averaged `{human_bytes(bytes_per_second)}/s` (`{human_bytes(bytes_per_second * 60.0)}/min`).
- Raw message bandwidth was dominated by `{by_class_rows[0]['message_class'] if by_class_rows else 'n/a'}` at `{pct(by_class_rows[0]['total_bytes'], total_raw_bytes) if by_class_rows else 0}%` of observed bytes.
- Heartbeat decoded bodies expanded to `{human_bytes(heartbeat_decoded_total)}` after `ENCODED_DATA` decompression, but those bytes were **not** used in the raw bandwidth totals.
- First half vs second half raw bytes: `{human_bytes(first_half_bytes)}` vs `{human_bytes(second_half_bytes)}`.

## Raw Byte Distribution
{msg_class_table}

{sender_table}

{stream_table}

## Top Raw Fields
{raw_field_table}

## Heartbeat Decoded Composition
These fields describe what sits inside compressed heartbeat bodies. They are useful for optimization analysis but are not additive with the raw heartbeat bytes above.

{hb_inner_field_table}

## Empty or Default-like Raw Fields
{render_table(
  ["field", "total estimated raw bytes"],
  [[item["field"], human_bytes(item["total_bytes"])] for item in top_empty_fields[:10]]
)}

## Largest Raw Message Examples
{os.linesep.join(largest_examples)}

## Stability Check
- first half message count / raw bytes: `{first_half_msgs}` / `{human_bytes(first_half_bytes)}`
- second half message count / raw bytes: `{second_half_msgs}` / `{human_bytes(second_half_bytes)}`
- top shape hashes: `{", ".join(f"{shape}:{count}" for shape, count in top_shapes)}`

## Raw Bytes By Minute
{os.linesep.join(minute_lines)}

## Artifacts
- capture jsonl: `{capture_file}`
- summary json: `{summary_json}`
- results md: `{results_md}`

## Verification
- command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds {args.seconds} --max-messages {args.max_messages}`
  result: `pass`
  evidence: `Captured {total_messages} raw MQTT payloads over {duration:.1f}s on {args.network}`
"""
  results_md.write_text(md, encoding="utf-8")
  return results


def build_artifacts(output_dir: Path) -> CaptureArtifacts:
  ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%z")
  output_dir.mkdir(parents=True, exist_ok=True)
  base = output_dir / f"{ts}_mainnet_bandwidth"
  return CaptureArtifacts(
    capture_file=base.with_suffix(".jsonl"),
    summary_json=Path(str(base) + "_summary.json"),
    results_md=Path(str(base) + "_results.md"),
    metadata_json=Path(str(base) + "_metadata.json"),
  )


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Passive Ratio1 SDK raw-bandwidth capture.")
  parser.add_argument("--seconds", type=int, default=600, help="Capture duration in seconds.")
  parser.add_argument("--max-messages", type=int, default=30000, help="Maximum number of messages to capture.")
  parser.add_argument("--network", default="mainnet", help="EVM network for the SDK session.")
  parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for local artifacts.")
  parser.add_argument("--session-name", default="sdk-bandwidth-capture", help="SDK session alias.")
  parser.add_argument("--analyze-only", action="store_true", help="Skip live capture and analyze an existing JSONL file.")
  parser.add_argument("--capture-file", help="Existing capture JSONL to analyze.")
  parser.add_argument("--metadata-file", help="Existing capture metadata JSON to analyze.")
  parser.add_argument("--results-md", help="Explicit markdown output path.")
  parser.add_argument("--summary-json", help="Explicit summary json output path.")
  parser.add_argument("--silent", action="store_true", help="Reduce SDK logs during live capture.")
  return parser.parse_args()


def run_capture(args: argparse.Namespace) -> CaptureArtifacts:
  artifacts = build_artifacts(Path(args.output_dir))
  recorder = CaptureRecorder(artifacts.capture_file)
  start_ts = time()
  metadata = {
    "start_ts": start_ts,
    "start_iso": utc_now_iso(),
    "network": args.network,
    "session_name": args.session_name,
    "stop_reason": "running",
  }
  artifacts.metadata_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

  session = None
  try:
    session = PassiveBandwidthMqttSession(
      name=args.session_name,
      silent=args.silent,
      auto_configuration=True,
      run_dauth=True,
      use_home_folder=False,
      local_cache_base_folder=".",
      evm_network=args.network,
      raw_record_callback=recorder.record,
    )
    deadline = start_ts + args.seconds
    while time() < deadline and recorder.count < args.max_messages:
      sleep(0.25)
    metadata["stop_reason"] = "max-messages" if recorder.count >= args.max_messages else "time-window"
  finally:
    metadata["end_ts"] = time()
    metadata["end_iso"] = utc_now_iso()
    metadata["duration_seconds"] = round(metadata["end_ts"] - start_ts, 3)
    metadata["message_count"] = recorder.count
    artifacts.metadata_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    recorder.close()
    if session is not None:
      session.close(wait_close=True)
  return artifacts


def main() -> int:
  args = parse_args()
  if args.analyze_only:
    if not args.capture_file:
      raise SystemExit("--capture-file is required with --analyze-only")
    capture_file = Path(args.capture_file)
    metadata_file = Path(args.metadata_file) if args.metadata_file else capture_file.with_name(capture_file.stem.replace("_results", "").replace("_summary", "") + "_metadata.json")
    results_md = Path(args.results_md) if args.results_md else capture_file.with_name(capture_file.stem + "_results.md")
    summary_json = Path(args.summary_json) if args.summary_json else capture_file.with_name(capture_file.stem + "_summary.json")
    analyze_capture(capture_file, metadata_file, results_md, summary_json, args)
    print(results_md)
    return 0

  artifacts = run_capture(args)
  analyze_capture(artifacts.capture_file, artifacts.metadata_json, artifacts.results_md, artifacts.summary_json, args)
  print(str(artifacts.results_md))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
