#!/usr/bin/env python3
"""
Stress-test BusinessManager._check_instances with synthetic plugin trees and configs.

Key features:
- Create a fictive plugin filesystem tree from a nested dictionary.
- Generate minimal plugin modules for given signatures.
- Run BusinessManager._check_instances and print timings.

Nested tree format:
{
  "dirA": [
    "file1.txt",
    {"dirB": ["file2.py", {"dirC": ["file3.md"]}]}
  ]
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import shutil
import tempfile
import random
from dataclasses import dataclass
from typing import Dict, List, Union, Sequence

Tree = Dict[str, List[Union["Tree", str]]]


@dataclass
class FakeOwner:
  is_supervisor_node: bool = True
  is_secured: bool = False
  evm_network: str | None = None
  __version__: str = "0.0.0"
  runs_in_docker: bool = False
  docker_source: str = "main"

  def set_loop_stage(self, s: str) -> None:
    # Keep minimal but visible if needed.
    return

  def get_pipelines_view(self):
    return {}


def camel_to_snake(s: str) -> str:
  # Mirror Logger.camel_to_snake behavior for deterministic file names.
  if s.isupper():
    return s.lower()
  out = ""
  for i, ch in enumerate(s):
    if ch.isupper() and i > 0:
      out += "_"
    out += ch.lower()
  return out.replace("__", "_")


def signature_to_class_name(signature: str, suffix: str) -> str:
  # _get_module_name_and_class uses name.replace('_','') + suffix
  base = signature.replace("_", "")
  return f"{base}{suffix}"


def ensure_init_py(path: str) -> None:
  os.makedirs(path, exist_ok=True)
  init_path = os.path.join(path, "__init__.py")
  if not os.path.exists(init_path):
    with open(init_path, "w", encoding="utf-8") as f:
      f.write("# auto-generated for test package\n")


def materialize_tree(tree: Tree, root: str) -> None:
  for dirname, items in tree.items():
    dir_path = os.path.join(root, dirname)
    os.makedirs(dir_path, exist_ok=True)
    # Make it package-like to allow importlib to find modules
    ensure_init_py(dir_path)
    for item in items:
      if isinstance(item, dict):
        materialize_tree(item, dir_path)
      elif isinstance(item, str):
        file_path = os.path.join(dir_path, item)
        if not os.path.exists(file_path):
          with open(file_path, "w", encoding="utf-8") as f:
            f.write("# dummy file\n")
      else:
        raise ValueError(f"Unsupported tree item type: {type(item)}")

def count_locations(tree: Tree) -> int:
  count = 0
  for _, items in tree.items():
    count += 1
    for item in items:
      if isinstance(item, dict):
        count += count_locations(item)
  return count


def write_plugin_module(module_dir: str, signature: str, suffix: str,
                        use_base_plugin: bool, use_cv_plugin: bool) -> str:
  file_name = camel_to_snake(signature) + ".py"
  class_name = signature_to_class_name(signature, suffix)
  module_path = os.path.join(module_dir, file_name)
  if use_cv_plugin:
    code = f"""\
from naeural_core.business.base.cv_plugin_executor import CVPluginExecutor

_CONFIG = CVPluginExecutor.CONFIG
__VER__ = "0.0.0"

class {class_name}(CVPluginExecutor):
  def start_thread(self):
    # Avoid starting background threads in tests
    self.thread = None
    return
"""
  elif use_base_plugin:
    code = f"""\
from naeural_core.business.base.base_plugin_biz import BasePluginExecutor

_CONFIG = BasePluginExecutor.CONFIG
__VER__ = "0.0.0"

class {class_name}(BasePluginExecutor):
  def start_thread(self):
    # Avoid starting background threads in tests
    self.thread = None
    return
"""
  else:
    code = f"""\
_CONFIG = {{}}
__VER__ = "0.0.0"

class {class_name}:
  def __init__(self, *args, **kwargs):
    self.cfg_runs_only_on_supervisor_node = False
    self.done_loop = False
    return

  def start_thread(self):
    self.thread = None
    return

  def maybe_update_instance_config(self, **kwargs):
    return

  def __repr__(self):
    return "{class_name}()"
"""
  with open(module_path, "w", encoding="utf-8") as f:
    f.write(code)
  return module_path


def build_fake_plugin_package(base_dir: str, package: str, subdir: str) -> str:
  pkg_root = os.path.join(base_dir, package)
  ensure_init_py(pkg_root)
  if subdir:
    pkg_sub = os.path.join(pkg_root, subdir)
    ensure_init_py(pkg_sub)
    return pkg_sub
  return pkg_root


def generate_signatures(n_unique: int) -> List[str]:
  # Deterministic uppercase signatures to match existing behavior
  return [f"PLG_{i:03d}" for i in range(1, n_unique + 1)]


def build_instances_config(signatures: List[str], total_instances: int) -> list:
  from naeural_core import constants as ct
  plugins = []
  if not signatures:
    return plugins
  per_sig = total_instances // len(signatures)
  remainder = total_instances % len(signatures)

  idx = 0
  for sig in signatures:
    n = per_sig + (1 if idx < remainder else 0)
    idx += 1
    instances = []
    for j in range(n):
      instance_id = f"{sig}_INST_{j+1:03d}"
      instances.append({
        ct.CONFIG_INSTANCE.K_INSTANCE_ID: instance_id,
        "DISABLED": False,
      })
    plugins.append({
      ct.CONFIG_PLUGIN.K_SIGNATURE: sig,
      ct.CONFIG_PLUGIN.K_INSTANCES: instances,
    })
  random.shuffle(plugins)
  return plugins


def build_streams_config(plugins: list, pipeline_name: str = "pipeline_test") -> dict:
  from naeural_core import constants as ct
  return {
    pipeline_name: {
      ct.CONFIG_STREAM.K_PLUGINS: plugins,
      ct.CONFIG_STREAM.K_INITIATOR_ID: "test_initiator",
      ct.CONFIG_STREAM.K_INITIATOR_ADDR: "local",
      ct.CONFIG_STREAM.K_MODIFIED_BY_ID: "test_modifier",
      ct.CONFIG_STREAM.K_MODIFIED_BY_ADDR: "local",
      ct.CONFIG_STREAM.K_SESSION_ID: "session_test",
    }
  }


def load_tree(path: str | None) -> Tree | None:
  if path is None:
    return None
  with open(path, "r", encoding="utf-8") as f:
    return json.load(f)

def parse_signatures_csv(csv_value: str | None) -> List[str]:
  if not csv_value:
    return []
  return [x.strip() for x in csv_value.split(",") if x.strip()]

def _build_file_names(count: int, exts: Sequence[str], prefix: str) -> List[str]:
  names: List[str] = []
  for i in range(count):
    ext = exts[i % len(exts)] if exts else ""
    names.append(f"{prefix}_{i:03d}{ext}")
  return names


def generate_tree(depth: int, dirs_per_level: int, files_per_dir: int,
                  file_exts: Sequence[str], full_breadth: bool,
                  rng: random.Random, level: int = 0) -> Tree:
  if depth <= 0:
    return {}

  if full_breadth:
    n_dirs = dirs_per_level
    n_files = files_per_dir
  else:
    n_dirs = rng.randint(0, dirs_per_level)
    n_files = rng.randint(0, files_per_dir)

  dir_name = f"dir_{level}_{rng.randint(0, 9999):04d}"
  items: List[Union[Tree, str]] = []
  items.extend(_build_file_names(n_files, file_exts, f"file_{level}"))

  if depth > 1:
    for _ in range(n_dirs):
      items.append(generate_tree(depth - 1, dirs_per_level, files_per_dir,
                                 file_exts, full_breadth, rng, level + 1))

  return {dir_name: items}


def main() -> int:
  parser = argparse.ArgumentParser(description="Test BusinessManager._check_instances")
  parser.add_argument("--total-fake-instances", type=int, default=20)
  parser.add_argument("--total-real-instances", type=int, default=120)
  parser.add_argument("--unique-signatures", type=int, default=15)
  parser.add_argument("--fake-fs", type=str, default=None,
                      help="Path to JSON file describing a fake filesystem tree")
  parser.add_argument("--gen-tree", action="store_true",
                      help="Generate a fake filesystem tree instead of loading JSON")
  parser.add_argument("--tree-depth", type=int, default=6)
  parser.add_argument("--dirs-per-level", type=int, default=5)
  parser.add_argument("--files-per-dir", type=int, default=3)
  # parser.add_argument("--file-exts", type=str, default=".py,.txt,.md")
  parser.add_argument("--file-exts", type=str, default=".py")
  parser.add_argument("--full-breadth", action="store_true",
                      help="If set, each directory has exactly files_per_dir and dirs_per_level")
  parser.add_argument("--seed", type=int, default=1337)
  parser.add_argument("--use-base-plugin", action="store_true",
                      help="Generate plugins that subclass BasePluginExecutor")
  parser.add_argument("--use-cv-plugin", action="store_true",
                      help="Generate plugins that subclass CVPluginExecutor")
  parser.add_argument("--real-signatures", type=str, default="",
                      help="Comma-separated list of real plugin signatures to include")
  parser.add_argument("--include-real-plugins", action="store_true",
                      help="Include real plugins in search paths")
  parser.add_argument("--no-threads", action="store_true",
                      help="Disable plugin threads by monkeypatching BasePluginExecutor.start_thread")
  parser.add_argument("--keep", action="store_true", help="Keep generated fake filesystem on disk")
  args = parser.parse_args()

  repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
  # repo_root = os.path.join(repo_root, "naeural_core")
  # print(f"repo_root: {repo_root}")
  # exit(-1)

  # Drop any previously imported naeural_core to force local reload
  for mod in list(sys.modules.keys()):
    if mod == "naeural_core" or mod.startswith("naeural_core."):
      del sys.modules[mod]


  # Ensure local repo has priority over any installed package
  if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

  from naeural_core import constants as ct
  from naeural_core import Logger
  from naeural_core.business.business_manager import BusinessManager

  # Create temp base and fake package structure
  base_dir = tempfile.mkdtemp(prefix="bm_check_instances_")
  package_name = "fake_biz_plugins"
  subdir = "biz"

  # Materialize optional fake tree to stress filesystem traversal
  tree = load_tree(args.fake_fs)
  if tree is None and args.gen_tree:
    exts = [x for x in args.file_exts.split(",") if x]
    rng = random.Random(args.seed)
    tree = generate_tree(args.tree_depth, args.dirs_per_level, args.files_per_dir,
                         exts, args.full_breadth, rng)
  if tree:
    materialize_tree(tree, base_dir)
    print(f"Generated fake filesystem locations: {count_locations(tree)}")

  plugin_dir = build_fake_plugin_package(base_dir, package_name, subdir)

  if args.use_cv_plugin and args.use_base_plugin:
    raise ValueError("Use only one of --use-base-plugin or --use-cv-plugin")

  if args.use_base_plugin or args.use_cv_plugin or args.include_real_plugins:
    # Pre-import serving modules to mirror production import order and avoid cold-start cycles.
    import naeural_core.serving  # noqa: F401
    import naeural_core.serving.ai_engines  # noqa: F401

  if args.no_threads:
    from naeural_core.business.base.base_plugin_biz import BasePluginExecutor
    def _noop_start_thread(self):
      self.thread = None
      return
    BasePluginExecutor.start_thread = _noop_start_thread

  # Create fake plugin modules
  fake_signatures = generate_signatures(args.unique_signatures)
  for sig in fake_signatures:
    write_plugin_module(plugin_dir, sig, ct.PLUGIN_SEARCH.SUFFIX_BIZ_PLUGINS,
                        use_base_plugin=args.use_base_plugin,
                        use_cv_plugin=args.use_cv_plugin)
  print(f"Generated fake plugin modules: {len(fake_signatures)}")

  real_signatures = parse_signatures_csv(args.real_signatures)
  if args.include_real_plugins and not real_signatures:
    raise ValueError("--include-real-plugins requires --real-signatures")

  if args.include_real_plugins:
    all_signatures = []
    for sig in fake_signatures + real_signatures:
      if sig not in all_signatures:
        all_signatures.append(sig)
  else:
    all_signatures = list(fake_signatures)

  # Prepare environment: ensure both repo root and temp base are importable
  sys.path.insert(0, repo_root)
  sys.path.insert(0, base_dir)

  # For local filesystem scanning, _get_plugin_by_name uses os.walk on relative paths.
  # We temporarily switch cwd to base_dir so it can discover fake_biz_plugins.
  orig_cwd = os.getcwd()
  os.chdir(base_dir)

  # Patch plugin search locations to point to our temp package (and optionally real locations)
  orig_loc = ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS
  orig_safe = ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS
  try:
    if args.include_real_plugins:
      ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS = orig_loc + [f"{package_name}.{subdir}"]
      ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS = orig_safe
    else:
      ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS = [f"{package_name}.{subdir}"]
      ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS = []

    log = Logger("BIZM_TEST", DEBUG=False)
    owner = FakeOwner()
    class _DummyBC:
      address = ""
    class _DummyR1FS:
      pass
    def _dummy_main_loop_resolution():
      return 0.1
    def _dummy_save_config(**kwargs):
      return None
    class _DummyNetMon:
      pass
    shmem = {
      ct.BLOCKCHAIN_MANAGER: _DummyBC(),
      ct.R1FS_ENGINE: _DummyR1FS(),
      ct.CALLBACKS.MAIN_LOOP_RESOLUTION_CALLBACK: _dummy_main_loop_resolution,
      ct.CALLBACKS.INSTANCE_CONFIG_SAVER_CALLBACK: _dummy_save_config,
      "network_monitor": _DummyNetMon(),
    }
    bm = BusinessManager(log=log, owner=owner, shmem=shmem, run_on_threads=True)

    if args.include_real_plugins:
      plugins_cfg = []
      plugins_cfg += build_instances_config(fake_signatures, args.total_fake_instances)
      plugins_cfg += build_instances_config(real_signatures, args.total_real_instances)
      streams_cfg = build_streams_config(plugins_cfg)
    else:
      plugins_cfg = build_instances_config(fake_signatures, args.total_fake_instances)
      streams_cfg = build_streams_config(plugins_cfg)

    bm._dct_config_streams = streams_cfg
    bm._check_instances()

  finally:
    ct.PLUGIN_SEARCH.LOC_BIZ_PLUGINS = orig_loc
    ct.PLUGIN_SEARCH.SAFE_BIZ_PLUGINS = orig_safe
    os.chdir(orig_cwd)
    if not args.keep:
      shutil.rmtree(base_dir, ignore_errors=True)

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
