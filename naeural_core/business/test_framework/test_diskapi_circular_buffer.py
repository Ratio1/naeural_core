import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

if 'cv2' not in sys.modules:
  sys.modules['cv2'] = types.SimpleNamespace(
    imread=lambda *args, **kwargs: None,
    imwrite=lambda *args, **kwargs: True,
    VideoWriter=lambda *args, **kwargs: None,
    VideoWriter_fourcc=lambda *args, **kwargs: 0,
  )

_DISKAPI_PATH = Path(__file__).resolve().parents[1] / 'mixins_base' / 'diskapi.py'
_DISKAPI_SPEC = importlib.util.spec_from_file_location('test_diskapi_module', _DISKAPI_PATH)
_DISKAPI_MODULE = importlib.util.module_from_spec(_DISKAPI_SPEC)
_DISKAPI_SPEC.loader.exec_module(_DISKAPI_MODULE)
_DiskAPIMixin = _DISKAPI_MODULE._DiskAPIMixin


class _FakeLog:
  """Minimal logger stub for focused diskapi buffer tests."""

  def __init__(self, base_dir):
    self._base_dir = base_dir
    self._target_folders = {
      'data': os.path.join(base_dir, '_data'),
      'models': os.path.join(base_dir, '_models'),
      'output': os.path.join(base_dir, '_output'),
    }
    for folder_path in self._target_folders.values():
      os.makedirs(folder_path, exist_ok=True)

  def get_base_folder(self):
    return self._base_dir

  def get_target_folder(self, folder):
    return self._target_folders[folder]

  def get_data_folder(self):
    return self._target_folders['data']

  def thread_safe_save(self, datafile, data_json, folder=None, locking=True, indent=True):
    _ = locking
    if folder is None:
      full_path = datafile
    else:
      full_path = os.path.join(self.get_target_folder(folder), datafile)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'w') as handle:
      json.dump(data_json, handle, sort_keys=True, indent=4 if indent else None)
    return full_path

  def load_json(
    self,
    fname,
    folder=None,
    subfolder_path=None,
    numeric_keys=True,
    locking=True,
    verbose=True,
  ):
    """
    Load one JSON payload while mimicking the live logger contract.

    Parameters
    ----------
    fname : str
      Relative or absolute JSON file name.

    folder : str, optional
      Local-cache root bucket. When omitted, `fname` is treated as an absolute
      or already resolved path.

    subfolder_path : str, optional
      Optional relative subfolder below `folder`.

    numeric_keys : bool, optional
      Included for interface parity with the live logger.

    locking : bool, optional
      Included for interface parity with the live logger.

    verbose : bool, optional
      Included for interface parity with the live logger.

    Returns
    -------
    Any
      Decoded JSON payload, or `None` when the file is missing or unreadable.
    """
    _ = numeric_keys, locking, verbose
    if folder is None:
      full_path = fname
    else:
      path_parts = [self.get_target_folder(folder)]
      if subfolder_path is not None:
        path_parts.append(subfolder_path)
      path_parts.append(fname)
      full_path = os.path.join(*path_parts)
    if not os.path.isfile(full_path):
      return None
    try:
      with open(full_path, 'r') as handle:
        return json.load(handle)
    except Exception:
      return None


class _DiskApiHarness(_DiskAPIMixin):
  """Small harness that exposes `_DiskAPIMixin` against a temp folder."""

  def __init__(self, base_dir):
    self.log = _FakeLog(base_dir)
    self.plugin_id = 'INSTANCE_A'
    self.messages = []
    super().__init__()

  def P(self, msg, color=None):
    _ = color
    self.messages.append(str(msg))
    return


class TestDiskApiCircularBuffer(unittest.TestCase):
  """Focused regressions for bounded JSON persistence in local cache."""

  def test_default_filename_uses_diskapi_plugin_buffer_path(self):
    """Omitted filenames should resolve to the default diskapi plugin buffer path."""
    with tempfile.TemporaryDirectory() as temp_dir:
      ctx = _DiskApiHarness(temp_dir)
      result = ctx.diskapi_save_json_circular_buffer(
        dct={'payload': 1},
        max_items=10,
      )
      loaded_payload = ctx.diskapi_load_json_circular_buffer()

      self.assertEqual('jsons_circular_buffer.json', result['filename'])
      self.assertTrue(
        result['saved_path'].endswith(
          os.path.join('_diskapi', 'INSTANCE_A', 'jsons_circular_buffer.json')
        )
      )
      self.assertEqual(1, len(loaded_payload))
      self.assertEqual(1, loaded_payload[0]['payload'])

  def test_single_file_buffer_keeps_only_latest_items(self):
    """The helper should roll a single JSON file forward with the newest items."""
    with tempfile.TemporaryDirectory() as temp_dir:
      ctx = _DiskApiHarness(temp_dir)
      filename = 'INSTANCE_A.json'
      subdir = 'SOL9'

      for second in range(12):
        result = ctx.diskapi_save_json_circular_buffer(
          dct={'second': second},
          max_items=10,
          filename=filename,
          folder='data',
          subdir=subdir,
        )

      saved_payload = ctx.diskapi_load_json_circular_buffer(
        filename=filename,
        folder='data',
        subdir=subdir,
      )
      entry_zero = ctx.diskapi_load_json_circular_buffer(
        entry_id=0,
        filename=filename,
        folder='data',
        subdir=subdir,
      )
      entry_last = ctx.diskapi_load_json_circular_buffer(
        entry_id=9,
        filename=filename,
        folder='data',
        subdir=subdir,
      )
      self.assertEqual(10, result['items_in_buffer'])
      self.assertIsInstance(saved_payload, list)
      self.assertEqual(10, len(saved_payload))
      self.assertEqual(2, saved_payload[0]['second'])
      self.assertEqual(11, saved_payload[-1]['second'])
      self.assertEqual(2, entry_zero['second'])
      self.assertEqual(11, entry_last['second'])

  def test_singleton_buffer_preserves_direct_dict_shape(self):
    """A one-item buffer should keep the historical dict-on-disk contract."""
    with tempfile.TemporaryDirectory() as temp_dir:
      ctx = _DiskApiHarness(temp_dir)
      subdir = os.path.join('VAC', 'VEAC02')
      result = ctx.diskapi_save_json_circular_buffer(
        dct={'slot': 2, 'images': {'2': {'track_id': 52}}},
        max_items=1,
        filename='images.json',
        folder='data',
        subdir=subdir,
      )

      saved_payload = ctx.diskapi_load_json_from_data(os.path.join(subdir, 'images.json'), verbose=False)
      loaded_buffer = ctx.diskapi_load_json_circular_buffer(
        filename='images.json',
        folder='data',
        subdir=subdir,
      )
      loaded_entry = ctx.diskapi_load_json_circular_buffer(
        entry_id=0,
        filename='images.json',
        folder='data',
        subdir=subdir,
      )
      self.assertEqual('images.json', result['filename'])
      self.assertEqual(1, result['items_in_buffer'])
      self.assertIsInstance(saved_payload, dict)
      self.assertEqual(52, saved_payload['images']['2']['track_id'])
      self.assertEqual(1, len(loaded_buffer))
      self.assertEqual(52, loaded_buffer[0]['images']['2']['track_id'])
      self.assertEqual(52, loaded_entry['images']['2']['track_id'])

  def test_missing_or_empty_buffer_returns_empty_result_shapes(self):
    """Missing or unreadable circular-buffer files should return empty shapes."""
    with tempfile.TemporaryDirectory() as temp_dir:
      ctx = _DiskApiHarness(temp_dir)
      missing_entries = ctx.diskapi_load_json_circular_buffer(
        filename='missing.json',
        folder='data',
        subdir='SOL9',
      )
      missing_entry = ctx.diskapi_load_json_circular_buffer(
        entry_id=0,
        filename='missing.json',
        folder='data',
        subdir='SOL9',
      )

      empty_path = os.path.join(temp_dir, '_data', 'SOL9', 'empty.json')
      os.makedirs(os.path.dirname(empty_path), exist_ok=True)
      with open(empty_path, 'w') as handle:
        handle.write('')

      empty_entries = ctx.diskapi_load_json_circular_buffer(
        filename='empty.json',
        folder='data',
        subdir='SOL9',
      )
      empty_entry = ctx.diskapi_load_json_circular_buffer(
        entry_id=0,
        filename='empty.json',
        folder='data',
        subdir='SOL9',
      )

      self.assertEqual([], missing_entries)
      self.assertIsNone(missing_entry)
      self.assertEqual([], empty_entries)
      self.assertIsNone(empty_entry)

  def test_out_of_range_entry_returns_none_and_logs_warning(self):
    """Entry lookups outside the buffer should return `None` and log context."""
    with tempfile.TemporaryDirectory() as temp_dir:
      ctx = _DiskApiHarness(temp_dir)

      for second in range(3):
        ctx.diskapi_save_json_circular_buffer(
          dct={'second': second},
          max_items=3,
          filename='window.json',
          folder='data',
          subdir='SOL9',
        )

      loaded_entry = ctx.diskapi_load_json_circular_buffer(
        entry_id=3,
        filename='window.json',
        folder='data',
        subdir='SOL9',
      )

      self.assertIsNone(loaded_entry)
      self.assertTrue(
        any('not found' in message for message in ctx.messages),
      )


if __name__ == '__main__':
  unittest.main()
