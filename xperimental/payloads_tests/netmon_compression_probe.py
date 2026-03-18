#!/usr/bin/env python3
"""
Passive NET_MON_01 compression probe using the same zlib+base64 codec as
heartbeat ENCODED_DATA.

This captures real mainnet NET_MON_01 payloads, stores full raw samples for
evidence, and estimates the size reduction for:
1. CURRENT_NETWORK-only compression
2. Heartbeat-style body compression of all non-EE_* payload fields
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import sleep, time
from typing import Any

try:
  from xperimental.payloads_tests.sdk_bandwidth_capture import (
    PassiveBandwidthMqttSession,
    compact_json_text,
    human_bytes,
    message_class,
    pct,
    percentile,
    render_sender_label,
    utc_now_iso,
  )
except ModuleNotFoundError:
  from sdk_bandwidth_capture import (  # type: ignore
    PassiveBandwidthMqttSession,
    compact_json_text,
    human_bytes,
    message_class,
    pct,
    percentile,
    render_sender_label,
    utc_now_iso,
  )


DEFAULT_OUTPUT_DIR = "xperimental/payloads_tests/evidence/netmon_compression"
CURRENT_NETWORK_VERSION = "zlib_b64_v1"
NETMON_BODY_VERSION = "v2"
TARGET_MESSAGE_CLASS = "payload:NET_MON_01"


def hb_like_compress_text(text: str) -> str:
  return base64.b64encode(zlib.compress(text.encode("utf-8"), level=9)).decode("utf-8")


def compact_json_bytes(value: Any) -> int:
  return len(compact_json_text(value).encode("utf-8"))


def latest_bandwidth_summary(default_glob: str) -> Path | None:
  matches = sorted(glob.glob(default_glob))
  if not matches:
    return None
  return Path(matches[-1])


@dataclass
class ProbeArtifacts:
  samples_jsonl: Path
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


class NetmonCompressionRecorder:
  def __init__(self, samples_path: Path):
    self.writer = JsonlWriter(samples_path)
    self.lock = Lock()
    self.count = 0
    self.first_recv_ts = None
    self.last_recv_ts = None

  def record(self, *, comm_type: str, topic: str, payload_bytes: bytes, log) -> None:
    recv_ts = time()
    raw_wire_bytes = len(payload_bytes)
    try:
      raw_text = payload_bytes.decode("utf-8")
      raw_obj = json.loads(raw_text)
    except Exception:
      return

    path = raw_obj.get("EE_PAYLOAD_PATH") or [None, None, None, None]
    signature = path[2] if isinstance(path, list) and len(path) > 2 else None
    event_type = raw_obj.get("EE_EVENT_TYPE")
    if message_class(event_type, signature, raw_obj) != TARGET_MESSAGE_CLASS:
      return

    sender_addr = raw_obj.get("EE_SENDER")
    sender_id = raw_obj.get("EE_ID")
    current_network = raw_obj.get("CURRENT_NETWORK") or {}
    current_network_text = compact_json_text(current_network)
    current_network_raw_bytes = len(current_network_text.encode("utf-8"))
    current_network_zlib_bytes = len(zlib.compress(current_network_text.encode("utf-8"), level=9))
    current_network_encoded = hb_like_compress_text(current_network_text)
    current_network_encoded_bytes = len(current_network_encoded.encode("utf-8"))

    current_network_only_payload = dict(raw_obj)
    current_network_only_payload["CURRENT_NETWORK"] = current_network_encoded
    current_network_only_payload["CURRENT_NETWORK_VERSION"] = CURRENT_NETWORK_VERSION
    current_network_only_payload_bytes = compact_json_bytes(current_network_only_payload)

    body = {
      key: value for key, value in raw_obj.items()
      if not str(key).startswith("EE_")
    }
    body_text = compact_json_text(body)
    body_raw_bytes = len(body_text.encode("utf-8"))
    body_encoded = hb_like_compress_text(body_text)
    body_encoded_bytes = len(body_encoded.encode("utf-8"))
    hb_like_payload = {
      key: value for key, value in raw_obj.items()
      if str(key).startswith("EE_")
    }
    hb_like_payload["NETMON_VERSION"] = NETMON_BODY_VERSION
    hb_like_payload["ENCODED_DATA"] = body_encoded
    hb_like_payload_bytes = compact_json_bytes(hb_like_payload)

    row = {
      "sample_index": None,
      "recv_ts": recv_ts,
      "recv_iso": datetime.fromtimestamp(recv_ts, tz=timezone.utc).isoformat(timespec="seconds"),
      "comm_type": comm_type,
      "topic": topic,
      "sender": render_sender_label(sender_id, sender_addr),
      "sender_id": sender_id,
      "sender_addr": sender_addr,
      "raw_wire_bytes": raw_wire_bytes,
      "raw_compact_bytes": compact_json_bytes(raw_obj),
      "payload_sha1": hashlib.sha1(payload_bytes).hexdigest()[:12],
      "current_network_raw_bytes": current_network_raw_bytes,
      "current_network_zlib_bytes": current_network_zlib_bytes,
      "current_network_encoded_bytes": current_network_encoded_bytes,
      "current_network_only_payload_bytes": current_network_only_payload_bytes,
      "current_network_only_reduction_bytes": raw_wire_bytes - current_network_only_payload_bytes,
      "current_network_only_reduction_pct": pct(raw_wire_bytes - current_network_only_payload_bytes, raw_wire_bytes),
      "hb_like_body_raw_bytes": body_raw_bytes,
      "hb_like_body_encoded_bytes": body_encoded_bytes,
      "hb_like_payload_bytes": hb_like_payload_bytes,
      "hb_like_reduction_bytes": raw_wire_bytes - hb_like_payload_bytes,
      "hb_like_reduction_pct": pct(raw_wire_bytes - hb_like_payload_bytes, raw_wire_bytes),
      "body_keys": sorted(body.keys()),
      "top_level_keys": sorted(raw_obj.keys()),
      "raw_payload": raw_obj,
    }

    with self.lock:
      self.count += 1
      row["sample_index"] = self.count
      if self.first_recv_ts is None:
        self.first_recv_ts = recv_ts
      self.last_recv_ts = recv_ts
    self.writer.write(row)

  def close(self) -> None:
    self.writer.close()


def build_artifacts(output_dir: Path) -> ProbeArtifacts:
  ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%z")
  output_dir.mkdir(parents=True, exist_ok=True)
  base = output_dir / f"{ts}_netmon_compression"
  return ProbeArtifacts(
    samples_jsonl=base.with_suffix(".jsonl"),
    summary_json=Path(str(base) + "_summary.json"),
    results_md=Path(str(base) + "_results.md"),
    metadata_json=Path(str(base) + "_metadata.json"),
  )


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
  lines = [
    "| " + " | ".join(headers) + " |",
    "| " + " | ".join(["---"] * len(headers)) + " |",
  ]
  for row in rows:
    lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
  return "\n".join(lines)


def analyze_samples(
  samples_jsonl: Path,
  metadata_path: Path,
  results_md: Path,
  summary_json: Path,
  bandwidth_summary_path: Path | None,
  args: argparse.Namespace,
) -> dict[str, Any]:
  rows = []
  with samples_jsonl.open("r", encoding="utf-8") as handle:
    for line in handle:
      rows.append(json.loads(line))

  metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
  raw_sizes = [row["raw_wire_bytes"] for row in rows]
  current_network_only_sizes = [row["current_network_only_payload_bytes"] for row in rows]
  hb_like_sizes = [row["hb_like_payload_bytes"] for row in rows]
  current_network_reduction_pcts = [row["current_network_only_reduction_pct"] for row in rows]
  hb_like_reduction_pcts = [row["hb_like_reduction_pct"] for row in rows]
  current_network_field_reduction_pcts = [
    pct(row["current_network_raw_bytes"] - row["current_network_encoded_bytes"], row["current_network_raw_bytes"])
    for row in rows
  ]

  totals = {
    "sample_count": len(rows),
    "raw_wire_bytes": sum(raw_sizes),
    "current_network_only_payload_bytes": sum(current_network_only_sizes),
    "hb_like_payload_bytes": sum(hb_like_sizes),
    "current_network_only_reduction_bytes": sum(row["current_network_only_reduction_bytes"] for row in rows),
    "hb_like_reduction_bytes": sum(row["hb_like_reduction_bytes"] for row in rows),
    "current_network_raw_bytes": sum(row["current_network_raw_bytes"] for row in rows),
    "current_network_encoded_bytes": sum(row["current_network_encoded_bytes"] for row in rows),
    "hb_like_body_raw_bytes": sum(row["hb_like_body_raw_bytes"] for row in rows),
    "hb_like_body_encoded_bytes": sum(row["hb_like_body_encoded_bytes"] for row in rows),
  }

  bandwidth_projection = None
  if bandwidth_summary_path is not None and bandwidth_summary_path.exists():
    bandwidth_summary = json.loads(bandwidth_summary_path.read_text(encoding="utf-8"))
    total_raw_bytes = bandwidth_summary["totals"]["raw_bytes"]
    netmon_row = next(
      (row for row in bandwidth_summary["by_class"] if row["message_class"] == TARGET_MESSAGE_CLASS),
      None,
    )
    if netmon_row is not None:
      netmon_raw_bytes = netmon_row["total_bytes"]
      avg_hb_like_reduction_pct = (
        totals["hb_like_reduction_bytes"] / totals["raw_wire_bytes"] * 100.0
        if totals["raw_wire_bytes"] > 0 else 0.0
      )
      avg_current_network_only_reduction_pct = (
        totals["current_network_only_reduction_bytes"] / totals["raw_wire_bytes"] * 100.0
        if totals["raw_wire_bytes"] > 0 else 0.0
      )
      bandwidth_projection = {
        "bandwidth_summary_path": str(bandwidth_summary_path),
        "ten_min_total_raw_bytes": total_raw_bytes,
        "ten_min_netmon_raw_bytes": netmon_raw_bytes,
        "hb_like_saved_bytes": int(round(netmon_raw_bytes * avg_hb_like_reduction_pct / 100.0)),
        "current_network_only_saved_bytes": int(round(netmon_raw_bytes * avg_current_network_only_reduction_pct / 100.0)),
      }
      bandwidth_projection["hb_like_saved_share_of_total"] = pct(
        bandwidth_projection["hb_like_saved_bytes"],
        total_raw_bytes,
      )
      bandwidth_projection["current_network_only_saved_share_of_total"] = pct(
        bandwidth_projection["current_network_only_saved_bytes"],
        total_raw_bytes,
      )

  results = {
    "scope": {
      "samples_jsonl": str(samples_jsonl),
      "summary_json": str(summary_json),
      "results_md": str(results_md),
      "sample_count": len(rows),
      "capture_seconds": metadata["duration_seconds"],
      "stop_reason": metadata["stop_reason"],
    },
    "totals": totals,
    "percentiles": {
      "raw_wire_bytes_p50": percentile(raw_sizes, 50),
      "raw_wire_bytes_p95": percentile(raw_sizes, 95),
      "current_network_only_reduction_pct_p50": percentile([int(round(x * 10)) for x in current_network_reduction_pcts], 50) / 10.0,
      "hb_like_reduction_pct_p50": percentile([int(round(x * 10)) for x in hb_like_reduction_pcts], 50) / 10.0,
    },
    "bandwidth_projection": bandwidth_projection,
    "largest_samples": sorted(rows, key=lambda row: row["raw_wire_bytes"], reverse=True)[:10],
  }
  summary_json.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")

  avg_current_network_only_reduction_pct = (
    totals["current_network_only_reduction_bytes"] / totals["raw_wire_bytes"] * 100.0
    if totals["raw_wire_bytes"] > 0 else 0.0
  )
  avg_hb_like_reduction_pct = (
    totals["hb_like_reduction_bytes"] / totals["raw_wire_bytes"] * 100.0
    if totals["raw_wire_bytes"] > 0 else 0.0
  )
  current_network_field_reduction_pct = (
    (totals["current_network_raw_bytes"] - totals["current_network_encoded_bytes"]) / totals["current_network_raw_bytes"] * 100.0
    if totals["current_network_raw_bytes"] > 0 else 0.0
  )

  scenario_table = render_table(
    ["scenario", "total bytes", "avg bytes/sample", "reduction vs raw"],
    [
      [
        "raw NET_MON_01",
        human_bytes(totals["raw_wire_bytes"]),
        int(totals["raw_wire_bytes"] / len(rows)) if rows else 0,
        "0.0%",
      ],
      [
        "CURRENT_NETWORK compressed",
        human_bytes(totals["current_network_only_payload_bytes"]),
        int(totals["current_network_only_payload_bytes"] / len(rows)) if rows else 0,
        f"{avg_current_network_only_reduction_pct:.1f}%",
      ],
      [
        "hb-style NET_MON body",
        human_bytes(totals["hb_like_payload_bytes"]),
        int(totals["hb_like_payload_bytes"] / len(rows)) if rows else 0,
        f"{avg_hb_like_reduction_pct:.1f}%",
      ],
    ],
  )

  field_table = render_table(
    ["field/body", "raw bytes", "hb-compatible encoded bytes", "reduction"],
    [
      [
        "CURRENT_NETWORK only",
        human_bytes(totals["current_network_raw_bytes"]),
        human_bytes(totals["current_network_encoded_bytes"]),
        f"{current_network_field_reduction_pct:.1f}%",
      ],
      [
        "all non-EE_* NET_MON fields",
        human_bytes(totals["hb_like_body_raw_bytes"]),
        human_bytes(totals["hb_like_body_encoded_bytes"]),
        f"{pct(totals['hb_like_body_raw_bytes'] - totals['hb_like_body_encoded_bytes'], totals['hb_like_body_raw_bytes'])}%",
      ],
    ],
  )

  sample_table = render_table(
    ["sample", "sender", "raw bytes", "CURRENT_NETWORK reduction", "hb-like reduction"],
    [
      [
        row["sample_index"],
        row["sender"],
        human_bytes(row["raw_wire_bytes"]),
        f"{row['current_network_only_reduction_pct']}%",
        f"{row['hb_like_reduction_pct']}%",
      ]
      for row in results["largest_samples"][:10]
    ],
  )

  projection_lines = ["- no 10-minute raw-bandwidth summary was available for extrapolation"]
  if bandwidth_projection is not None:
    projection_lines = [
      f"- source 10-minute summary: `{bandwidth_projection['bandwidth_summary_path']}`",
      f"- `NET_MON_01` raw bytes in that 10-minute run: `{human_bytes(bandwidth_projection['ten_min_netmon_raw_bytes'])}`",
      f"- projected save with CURRENT_NETWORK-only compression: `{human_bytes(bandwidth_projection['current_network_only_saved_bytes'])}` "
      f"({bandwidth_projection['current_network_only_saved_share_of_total']}% of the full 10-minute raw sample)",
      f"- projected save with hb-style NET_MON body compression: `{human_bytes(bandwidth_projection['hb_like_saved_bytes'])}` "
      f"({bandwidth_projection['hb_like_saved_share_of_total']}% of the full 10-minute raw sample)",
    ]

  md = f"""# NET_MON Compression Probe Results

## Scope
- script path: `xperimental/payloads_tests/netmon_compression_probe.py`
- capture command: `python3 xperimental/payloads_tests/netmon_compression_probe.py --seconds {args.seconds} --target-count {args.target_count}`
- sample count: `{len(rows)}`
- capture window: `{metadata['start_iso']}` to `{metadata['end_iso']}` ({metadata['duration_seconds']:.1f}s)
- stop reason: `{metadata['stop_reason']}`

## Codec
- heartbeat-compatible codec: `zlib.compress(level=9)` + `base64.b64encode`
- reference implementation: `ratio1/logging/logger_mixins/general_serialization_mixin.py`
- note: payload-size estimates keep the outer JSON envelope and approximate post-change wire bytes by compact JSON serialization; `EE_SIGN` / `EE_HASH` values would change, but their lengths stay effectively constant

## Executive Summary
- Average raw NET_MON_01 size in this sample was `{human_bytes(totals['raw_wire_bytes'] / len(rows)) if rows else '0B'}`.
- Compressing `CURRENT_NETWORK` alone reduced payload bytes by `{avg_current_network_only_reduction_pct:.1f}%` across the sampled NET_MON payloads.
- Compressing the full non-`EE_*` NET_MON body in a heartbeat-style `ENCODED_DATA` envelope reduced payload bytes by `{avg_hb_like_reduction_pct:.1f}%`.
- The raw `CURRENT_NETWORK` field itself shrank by `{current_network_field_reduction_pct:.1f}%` when encoded with the heartbeat codec.

## Scenario Totals
{scenario_table}

## Field/Body Compression
{field_table}

## Largest Samples
{sample_table}

## Reduction Spread
- `CURRENT_NETWORK`-only reduction p50: `{percentile([int(round(x * 10)) for x in current_network_reduction_pcts], 50) / 10.0}%`
- `CURRENT_NETWORK`-only reduction p95: `{percentile([int(round(x * 10)) for x in current_network_reduction_pcts], 95) / 10.0}%`
- hb-style NET_MON-body reduction p50: `{percentile([int(round(x * 10)) for x in hb_like_reduction_pcts], 50) / 10.0}%`
- hb-style NETMON-body reduction p95: `{percentile([int(round(x * 10)) for x in hb_like_reduction_pcts], 95) / 10.0}%`
- raw `CURRENT_NETWORK` field reduction p50: `{percentile([int(round(x * 10)) for x in current_network_field_reduction_pcts], 50) / 10.0}%`

## Projection Onto The 10-Minute Raw Bandwidth Run
{chr(10).join(projection_lines)}

## Artifacts
- samples jsonl: `{samples_jsonl}`
- summary json: `{summary_json}`
- results md: `{results_md}`

## Verification
- command: `python3 xperimental/payloads_tests/netmon_compression_probe.py --seconds {args.seconds} --target-count {args.target_count}`
  result: `pass`
  evidence: `Captured {len(rows)} NET_MON_01 payloads and computed hb-style compression estimates`
"""
  results_md.write_text(md, encoding="utf-8")
  return results


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Passive NET_MON_01 compression probe.")
  parser.add_argument("--seconds", type=int, default=180, help="Capture duration in seconds.")
  parser.add_argument("--target-count", type=int, default=30, help="Number of NET_MON_01 payloads to sample.")
  parser.add_argument("--network", default="mainnet", help="EVM network for the SDK session.")
  parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for persistent evidence artifacts.")
  parser.add_argument("--session-name", default="netmon-compression-probe", help="SDK session alias.")
  parser.add_argument("--bandwidth-summary", help="Existing raw-bandwidth summary JSON for extrapolation.")
  parser.add_argument("--analyze-only", action="store_true", help="Skip live capture and analyze an existing samples JSONL.")
  parser.add_argument("--samples-jsonl", help="Existing NET_MON sample JSONL to analyze.")
  parser.add_argument("--metadata-file", help="Existing NET_MON sample metadata JSON to analyze.")
  parser.add_argument("--results-md", help="Explicit markdown output path.")
  parser.add_argument("--summary-json", help="Explicit summary json output path.")
  parser.add_argument("--silent", action="store_true", help="Reduce SDK logs during live capture.")
  return parser.parse_args()


def run_capture(args: argparse.Namespace) -> ProbeArtifacts:
  artifacts = build_artifacts(Path(args.output_dir))
  recorder = NetmonCompressionRecorder(artifacts.samples_jsonl)
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
    while time() < deadline and recorder.count < args.target_count:
      sleep(0.25)
    metadata["stop_reason"] = "target-count" if recorder.count >= args.target_count else "time-window"
  finally:
    metadata["end_ts"] = time()
    metadata["end_iso"] = utc_now_iso()
    metadata["duration_seconds"] = round(metadata["end_ts"] - start_ts, 3)
    metadata["sample_count"] = recorder.count
    artifacts.metadata_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    recorder.close()
    if session is not None:
      session.close(wait_close=True)
  return artifacts


def main() -> int:
  args = parse_args()
  bandwidth_summary = Path(args.bandwidth_summary) if args.bandwidth_summary else latest_bandwidth_summary(
    "xperimental/payloads_tests/evidence/raw_bandwidth/*_mainnet_bandwidth_summary.json"
  )

  if args.analyze_only:
    if not args.samples_jsonl:
      raise SystemExit("--samples-jsonl is required with --analyze-only")
    samples_jsonl = Path(args.samples_jsonl)
    metadata_file = Path(args.metadata_file) if args.metadata_file else samples_jsonl.with_name(samples_jsonl.stem + "_metadata.json")
    results_md = Path(args.results_md) if args.results_md else samples_jsonl.with_name(samples_jsonl.stem + "_results.md")
    summary_json = Path(args.summary_json) if args.summary_json else samples_jsonl.with_name(samples_jsonl.stem + "_summary.json")
    analyze_samples(samples_jsonl, metadata_file, results_md, summary_json, bandwidth_summary, args)
    print(results_md)
    return 0

  artifacts = run_capture(args)
  analyze_samples(artifacts.samples_jsonl, artifacts.metadata_json, artifacts.results_md, artifacts.summary_json, bandwidth_summary, args)
  print(artifacts.results_md)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
