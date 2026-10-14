"""Strict lifecycle tests using tiny, uniquely named OS shared-memory objects.

The complete utility source is loaded with only its unrelated runtime base class
stubbed. POSIX adapter tests exercise real mmap/SHM storage without requiring the
optional posix_ipc package; no production names or production-sized data are used.
"""

import importlib.util
import pathlib
import sys
import types
import unittest
import uuid
from multiprocessing import shared_memory
from unittest import mock

import numpy as np


_ROOT = pathlib.Path(__file__).resolve().parent


def _load(name, path):
  """Load the complete implementation without runtime startup imports."""
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _Base:
  """Supply only the runtime logging and automatic startup interface."""

  def __init__(self, log):
    self.log = log
    self.startup()

  def startup(self):
    pass

  def P(self, message, **kwargs):
    self.log.lines.append(message)

  def _create_notification(self, **kwargs):
    self.log.notifications.append(kwargs)


_package = types.ModuleType("naeural_core")
_package.DecentrAIObject = _Base
_DIAG = _load("shm_lifecycle_diag", _ROOT / "shm_diagnostics.py")
with mock.patch.dict(sys.modules, {
  "naeural_core": _package,
  "naeural_core.utils.shm_diagnostics": _DIAG,
}):
  _SHM = _load("shm_lifecycle_subject", _ROOT / "system_shared_memory.py")


class _ExistentialError(Exception):
  """Mirror posix_ipc's missing/existing-name error interface."""


class _PosixHandle:
  """Adapt real tiny OS SHM to posix_ipc's fd, close_fd and unlink API."""

  def __init__(self, name, flags, size):
    try:
      self.handle = shared_memory.SharedMemory(name=name, create=bool(flags), size=size)
    except (FileExistsError, FileNotFoundError) as exc:
      raise _ExistentialError(str(exc)) from exc
    self.fd = self.handle._fd
    self.size = self.handle.size

  def close_fd(self):
    self.handle.close()

  def unlink(self):
    try:
      self.handle.unlink()
    except FileNotFoundError as exc:
      raise _ExistentialError(str(exc)) from exc


_POSIX = types.SimpleNamespace(O_CREX=192, ExistentialError=_ExistentialError,
                               SharedMemory=_PosixHandle)


class SharedMemoryLifecycleTests(unittest.TestCase):
  """Prove ownership, rollback and repeat-safe cleanup through actual OS names."""

  def setUp(self):
    self.name = "ratio1_test_lifecycle_" + uuid.uuid4().hex
    self.log = types.SimpleNamespace(python_minor=11, lines=[], notifications=[])

  def _raw(self, suffix="", size=8):
    """Create a test-owned object and always remove its test name afterward."""
    handle = shared_memory.SharedMemory(name=self.name + suffix, create=True, size=size)
    self.addCleanup(self._dispose, handle)
    return handle

  @staticmethod
  def _dispose(handle):
    """Best-effort final test cleanup for already-unlinked real test resources."""
    handle.close()
    try:
      handle.unlink()
    except FileNotFoundError:
      pass

  def _subject(self, create=True, **kwargs):
    """Initialize at most a few hundred bytes and register repeat-safe shutdown."""
    options = dict(mem_name=self.name, np_shape=(4,), np_type=np.uint8,
                   is_buffer=True, maxlen=2, log=self.log, create=create)
    options.update(kwargs)
    subject = _SHM.NumpySharedMemory(**options)
    self.addCleanup(subject.shutdown)
    return subject

  def _missing(self, suffix=""):
    """Assert that a transaction removed its own exact kernel name."""
    with self.assertRaises(FileNotFoundError):
      handle = shared_memory.SharedMemory(name=self.name + suffix)
      self.addCleanup(handle.close)

  def test_fresh_creator_and_attacher_preserve_control(self):
    owner = self._subject()
    self.assertTrue(owner.initialized)
    self.assertEqual(owner.instance_type, "Creator")
    self.assertFalse(owner._np_mem_additional.any())
    slot = owner.add_data_to_buffer(np.array([9, 8, 7], dtype=np.uint8))
    user = self._subject(create=False)
    self.assertTrue(user.initialized)
    self.assertFalse(user.initiator)
    self.assertEqual(user._np_mem_additional[slot, 0], 1)
    np.testing.assert_array_equal(user.read_buffer_data(slot), [9, 8, 7])
    self.assertFalse(owner._np_mem_additional.any())
    user.shutdown()
    self.assertIsNotNone(owner._shm)
    owner.shutdown()
    self._missing()
    self._missing("_additional")

  def test_creator_initializes_only_metadata(self):
    original = shared_memory.SharedMemory

    def acquire(**kwargs):
      handle = original(**kwargs)
      handle.buf[:] = b"x" * handle.size
      return handle

    with mock.patch.object(shared_memory, "SharedMemory", side_effect=acquire):
      subject = self._subject()
    self.assertFalse(subject._np_mem_additional.any())
    self.assertEqual(bytes(subject._shm.buf), b"x" * 8)

  def test_success_reporting_failure_rolls_back_constructor_allocation(self):
    original_lines = self.log.lines
    failures = (RuntimeError("success logging failed"),
                KeyboardInterrupt("success interrupt"), SystemExit("success exit"))
    for failure in failures:
      with self.subTest(exception=type(failure).__name__):
        class Lines:
          def append(self, message):
            if "successfully initialized" in message:
              raise failure
            original_lines.append(message)

        self.log.lines = Lines()
        subject = object.__new__(_SHM.NumpySharedMemory)
        self.addCleanup(subject.shutdown)
        with self.assertRaises(type(failure)) as raised:
          subject.__init__(mem_name=self.name, np_shape=(4,), np_type=np.uint8,
                           create=True, is_buffer=True, maxlen=2, log=self.log)
        self.assertIs(raised.exception, failure)
        self.assertFalse(subject.initialized)
        self.assertIsNone(subject._shm)
        self.assertIsNone(subject._shm_additional)
        self._missing()
        self._missing("_additional")

  def test_primary_collision_preserves_foreign_bytes_and_names(self):
    foreign = self._raw()
    foreign.buf[:] = b"foreign!"
    with mock.patch.object(_SHM.np, "zeros", side_effect=AssertionError("private fallback")):
      subject = self._subject()
    self.assertFalse(subject.initialized)
    self.assertTrue(subject.initiator)
    self.assertEqual(subject.error_type, "FileExistsError")
    self.assertIsNone(subject._np_mem)
    subject.shutdown()
    self.assertEqual(bytes(foreign.buf), b"foreign!")
    self._missing("_additional")

  def test_metadata_collision_rolls_back_primary_only(self):
    foreign = self._raw("_additional", size=88)
    foreign.buf[:] = b"x" * 88
    subject = self._subject()
    self.assertEqual(subject.error_type, "FileExistsError")
    self.assertTrue(subject.initiator)
    self._missing()
    subject.shutdown()
    self.assertEqual(bytes(foreign.buf), b"x" * 88)

  def test_missing_attacher_never_creates(self):
    subject = self._subject(create=False)
    self.assertFalse(subject.initialized)
    self.assertFalse(subject.initiator)
    self.assertEqual(subject.error_type, "FileNotFoundError")
    self._missing()
    self._missing("_additional")

  def test_partial_attacher_closes_primary_without_unlink(self):
    foreign = self._raw()
    foreign.buf[:] = b"preserve"
    subject = self._subject(create=False)
    self.assertEqual(subject.error_type, "FileNotFoundError")
    self.assertIsNone(subject._shm)
    self.assertEqual(bytes(foreign.buf), b"preserve")

  def test_permission_failure_is_not_a_collision_or_role_fallback(self):
    for create in (True, False):
      with self.subTest(create=create):
        with mock.patch.object(shared_memory, "SharedMemory", side_effect=PermissionError("denied")) as constructor:
          subject = self._subject(create=create)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(subject.initiator, create)
        self.assertEqual(subject.error_type, "PermissionError")
        self.assertFalse(subject.initialized)

  def test_both_handles_acquired_before_view_failure_and_rolled_back(self):
    opened = []
    original = shared_memory.SharedMemory

    def acquire(**kwargs):
      handle = original(**kwargs)
      opened.append(handle)
      return handle

    def fail_view(*args, **kwargs):
      self.assertEqual(len(opened), 2)
      raise RuntimeError("view construction")

    with mock.patch.object(shared_memory, "SharedMemory", side_effect=acquire), \
         mock.patch.object(_SHM.np, "ndarray", side_effect=fail_view):
      subject = self._subject()
    self.assertEqual(subject.error_type, "RuntimeError")
    self.assertTrue(subject.initiator)
    self.assertTrue(all(handle._buf is None for handle in opened))
    self._missing()
    self._missing("_additional")

  def test_control_flow_exception_after_primary_rolls_back_and_reraises(self):
    original = shared_memory.SharedMemory
    for exception in (KeyboardInterrupt("stop allocation"), SystemExit("stop allocation")):
      with self.subTest(exception=type(exception).__name__):
        acquired = []

        def acquire(**kwargs):
          if acquired:
            raise exception
          handle = original(**kwargs)
          acquired.append(handle)
          return handle

        with mock.patch.object(shared_memory, "SharedMemory", side_effect=acquire):
          with self.assertRaises(type(exception)) as raised:
            self._subject()
        self.assertIs(raised.exception, exception)
        self.assertTrue(all(handle._buf is None for handle in acquired))
        self._missing()
        self._missing("_additional")

  def test_cleanup_reporting_failure_preserves_original_allocation_failure(self):
    original = shared_memory.SharedMemory
    original_lines = self.log.lines

    class Lines:
      def append(self, message):
        if message.startswith("Shared memory cleanup failures"):
          raise OSError("cleanup reporting failed")
        original_lines.append(message)

    self.log.lines = Lines()
    failures = (PermissionError("original control allocation denied"),
                KeyboardInterrupt("original interrupt"), SystemExit("original exit"))
    for failure in failures:
      with self.subTest(exception=type(failure).__name__):
        acquired = []

        def acquire(**kwargs):
          if acquired:
            raise failure
          handle = original(**kwargs)
          acquired.append(handle)
          proxy = mock.Mock(wraps=handle)
          proxy.size = handle.size
          proxy.close.side_effect = BufferError("injected close failure")
          return proxy

        # Keep the partial object inspectable even when __init__ re-raises an
        # interrupt, so both original fields and retryable cleanup are verified.
        subject = object.__new__(_SHM.NumpySharedMemory)
        self.addCleanup(subject.shutdown)
        options = dict(mem_name=self.name, np_shape=(4,), np_type=np.uint8,
                       is_buffer=True, maxlen=2, log=self.log, create=True)
        with mock.patch.object(shared_memory, "SharedMemory", side_effect=acquire):
          if isinstance(failure, Exception):
            subject.__init__(**options)
          else:
            with self.assertRaises(type(failure)) as raised:
              subject.__init__(**options)
            self.assertIs(raised.exception, failure)
        self.assertFalse(subject.initialized)
        self.assertEqual(subject.error_type, type(failure).__name__)
        self.assertIn(str(failure), subject.error_message)
        self.assertNotIn("cleanup reporting failed", subject.error_message)
        self.assertEqual(subject.cleanup_errors[-1]["exception_type"], "BufferError")
        subject._shm.close.side_effect = None
        subject.shutdown()
        self._missing()
        self._missing("_additional")

  def test_failure_reporting_sinks_preserve_original_error_and_pair_rollback(self):
    original_lines = self.log.lines
    for sink in ("log", "notification", "both"):
      with self.subTest(sink=sink):
        reports = []

        class Lines:
          def append(self, message):
            if message.startswith("ERROR in"):
              reports.append("log")
              if sink in ("log", "both"):
                raise OSError("failure log sink")
            original_lines.append(message)

        def notify(**kwargs):
          reports.append("notification")
          if sink in ("notification", "both"):
            raise OSError("failure notification sink")

        self.log.lines = Lines()
        with mock.patch.object(_SHM.np, "ndarray", side_effect=PermissionError("original view denied")), \
             mock.patch.object(_Base, "_create_notification", side_effect=notify):
          subject = self._subject()
        self.assertFalse(subject.initialized)
        self.assertEqual(subject.error_type, "PermissionError")
        self.assertIn("original view denied", subject.error_message)
        self.assertNotIn("sink", subject.error_message)
        self.assertEqual(reports, ["log", "notification"])
        self.assertEqual(subject.cleanup_errors, [])
        self.assertIsNone(subject._shm)
        self.assertIsNone(subject._shm_additional)
        self._missing()
        self._missing("_additional")

  def test_second_view_failure_rolls_back_existing_first_view(self):
    ndarray = np.ndarray
    calls = []

    def construct(*args, **kwargs):
      calls.append(1)
      if len(calls) == 2:
        raise RuntimeError("control view construction")
      return ndarray(*args, **kwargs)

    with mock.patch.object(_SHM.np, "ndarray", side_effect=construct):
      subject = self._subject()
    self.assertEqual(subject.error_type, "RuntimeError")
    self.assertIsNone(subject._np_mem)
    self.assertEqual(subject.cleanup_errors, [])
    self._missing()
    self._missing("_additional")

  def test_small_data_attachment_rejected_before_any_view(self):
    foreign = self._raw(size=2)
    with mock.patch.object(_SHM.np, "ndarray", side_effect=AssertionError("must validate first")):
      subject = self._subject(create=False)
    self.assertEqual(subject.error_type, "ValueError")
    self.assertFalse(subject.initiator)
    self.assertEqual(foreign.size, 2)

  def test_small_control_attachment_closes_both_without_unlink(self):
    self._raw()
    foreign = self._raw("_additional", size=4)
    with mock.patch.object(_SHM.np, "ndarray", side_effect=AssertionError("must validate pair first")):
      subject = self._subject(create=False)
    self.assertEqual(subject.error_type, "ValueError")
    self.assertIsNone(subject._shm)
    self.assertIsNone(subject._shm_additional)
    self.assertEqual(foreign.size, 4)

  def test_oversized_external_segments_remain_compatible(self):
    foreign = self._raw(size=16)
    control = self._raw("_additional", size=100)
    foreign.buf[:] = b"0123456789abcdef"
    control.buf[:] = b"x" * 100
    subject = self._subject(create=False)
    self.assertTrue(subject.initialized)
    self.assertEqual(subject._np_mem.shape, (2, 4))
    self.assertEqual(bytes(control.buf), b"x" * 100)
    subject.shutdown()
    self.assertEqual(bytes(foreign.buf), b"0123456789abcdef")

  def test_single_zone_fixed_name_and_repeat_shutdown(self):
    owner = self._subject(is_buffer=False)
    user = self._subject(create=False, is_buffer=False)
    self.assertTrue(owner.write(np.array([1, 2, 3, 4], dtype=np.uint8)))
    np.testing.assert_array_equal(user.read(), [1, 2, 3, 4])
    user.shutdown()
    user.shutdown()
    owner.shutdown()
    owner.shutdown()
    self.assertEqual(owner.cleanup_errors, [])
    self._missing()

  def test_close_failure_still_unlinks_both_and_repeat_is_foreign_safe(self):
    subject = self._subject()
    original = subject._shm
    failing = mock.Mock(wraps=original)
    attempts = []

    def close():
      attempts.append(1)
      if len(attempts) == 1:
        raise BufferError("exported view")
      original.close()

    failing.close.side_effect = close
    subject._shm = failing
    subject.shutdown()
    self.assertEqual(subject.cleanup_errors, [{"segment": self.name, "operation": "close",
                                              "exception_type": "BufferError"}])
    self._missing()
    self._missing("_additional")
    foreign = self._raw()
    foreign.buf[:] = b"newowner"
    subject.shutdown()
    self.assertEqual(failing.unlink.call_count, 1)
    self.assertEqual(bytes(foreign.buf), b"newowner")

  def test_shutdown_interrupt_attempts_both_resources_then_reraises_first(self):
    subject = self._subject()
    primary = mock.Mock(wraps=subject._shm)
    control = mock.Mock(wraps=subject._shm_additional)
    first = KeyboardInterrupt("primary close interrupt")
    primary.close.side_effect = first
    control.close.side_effect = SystemExit("control close interrupt")
    subject._shm = primary
    subject._shm_additional = control
    original_lines = self.log.lines

    class Lines:
      def append(self, message):
        if message.startswith("Shared memory cleanup failures"):
          raise RuntimeError("cleanup reporting failed")
        original_lines.append(message)

    self.log.lines = Lines()
    with self.assertRaises(KeyboardInterrupt) as raised:
      subject.shutdown()
    self.assertIs(raised.exception, first)
    self.assertEqual(primary.close.call_count, 1)
    self.assertEqual(control.close.call_count, 1)
    self.assertEqual(primary.unlink.call_count, 1)
    self.assertEqual(control.unlink.call_count, 1)
    self.assertEqual([entry["exception_type"] for entry in subject.cleanup_errors],
                     ["KeyboardInterrupt", "SystemExit"])
    self._missing()
    self._missing("_additional")
    primary.close.side_effect = None
    control.close.side_effect = None
    subject.shutdown()
    self.assertIsNone(subject._shm)
    self.assertIsNone(subject._shm_additional)

  def test_unlink_failure_remains_retryable_and_does_not_skip_control(self):
    subject = self._subject()
    original = subject._shm
    failing = mock.Mock(wraps=original)
    failing.unlink.side_effect = PermissionError("denied")
    subject._shm = failing
    subject.shutdown()
    self._missing("_additional")
    self.assertTrue(subject._owns_shm)
    self.assertEqual(subject.cleanup_errors[-1]["operation"], "unlink")
    failing.unlink.side_effect = None
    subject.shutdown()
    self._missing()
    self.assertFalse(subject._owns_shm)

  def test_bounded_scalar_cleanup_errors(self):
    subject = self._subject()
    original = subject._shm
    failing = mock.Mock(wraps=original)
    failing.unlink.side_effect = PermissionError("denied")
    subject._shm = failing
    for _ in range(40):
      subject.shutdown()
    self.assertEqual(len(subject.cleanup_errors), 32)
    self.assertEqual(subject.cleanup_errors_dropped, 8)
    self.assertTrue(all(type(value) is str for row in subject.cleanup_errors for value in row.values()))
    failing.unlink.side_effect = None
    subject.shutdown()

  def test_100_tiny_slots_admit_then_consume(self):
    subject = self._subject(maxlen=100)
    for index in range(100):
      self.assertEqual(subject.add_data_to_buffer(np.array([index], dtype=np.uint8)), index)
    self.assertIsNone(subject.add_data_to_buffer(np.array([100], dtype=np.uint8)))
    for index in range(100):
      np.testing.assert_array_equal(subject.read_buffer_data(index), [index])
    self.assertFalse(subject._np_mem_additional.any())

  def test_posix_strict_create_attach_metadata_and_cleanup(self):
    self.log.python_minor = 7
    with mock.patch.dict(sys.modules, {"posix_ipc": _POSIX}):
      owner = self._subject()
      self.assertTrue(owner.initialized)
      owner._np_mem_additional[0, 0] = 1
      user = self._subject(create=False)
      self.assertEqual(user._np_mem_additional[0, 0], 1)
      collision = self._subject()
      self.assertEqual(collision.error_type, "FileExistsError")
      self.assertTrue(collision.initiator)
      user.shutdown()
      owner.shutdown()
      self._missing()
      self._missing("_additional")

  def test_posix_mapping_failure_closes_acquired_fds(self):
    self.log.python_minor = 7
    import mmap
    acquired = [_PosixHandle(self.name, 192, 8),
                _PosixHandle(self.name + "_additional", 192, 88)]
    remaining = list(acquired)
    for handle in acquired:
      self.addCleanup(self._dispose, handle.handle)

    def acquire(**kwargs):
      # SharedMemory also uses mmap internally. Pre-acquire adapter fds before
      # injecting the failure specifically into the utility's retained backend.
      return remaining.pop(0)

    backend = types.SimpleNamespace(O_CREX=192, ExistentialError=_ExistentialError,
                                    SharedMemory=acquire)
    with mock.patch.dict(sys.modules, {"posix_ipc": backend}), \
         mock.patch.object(mmap, "mmap", side_effect=PermissionError("mapping")):
      subject = self._subject()
    self.assertEqual(subject.error_type, "PermissionError")
    self.assertEqual(len(acquired), 2)
    self.assertTrue(all(handle.handle._fd == -1 for handle in acquired))
    self.assertEqual(subject.cleanup_errors, [])
    self._missing()
    self._missing("_additional")

  def test_posix_cleanup_already_absent_is_repeat_safe(self):
    self.log.python_minor = 7
    with mock.patch.dict(sys.modules, {"posix_ipc": _POSIX}):
      subject = self._subject()
      subject._shm.unlink()
      subject._shm_additional.unlink()
      subject.shutdown()
      subject.shutdown()
    self.assertEqual(subject.cleanup_errors, [])

  def test_posix_missing_attach_and_control_collision(self):
    self.log.python_minor = 7
    with mock.patch.dict(sys.modules, {"posix_ipc": _POSIX}):
      user = self._subject(create=False)
      self.assertEqual(user.error_type, "FileNotFoundError")
      self.assertFalse(user.initiator)
      foreign = self._raw("_additional", size=88)
      foreign.buf[:] = b"f" * 88
      owner = self._subject()
      self.assertEqual(owner.error_type, "FileExistsError")
      self._missing()
      self.assertEqual(bytes(foreign.buf), b"f" * 88)


if __name__ == "__main__":
  unittest.main()
