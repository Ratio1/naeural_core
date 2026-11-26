"""Real-import admission regressions using tiny, test-owned OS SHM allocations.

These tests run against the installed core and its real SDK/NumPy dependencies.
They do not replace the runtime base class, extract method bodies or use live data.
"""

import unittest
import uuid
from multiprocessing import shared_memory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from naeural_core import Logger
from naeural_core.utils.system_shared_memory import NumpySharedMemory


class SharedMemoryAdmissionTests(unittest.TestCase):
  """Pin admission parity, ordinary transfers and unchanged slot ownership."""

  @classmethod
  def setUpClass(cls):
    """Use the real logger without creating persistent runtime directories."""
    cls.log = Logger(
      'SHM_ADMISSION', base_folder='.', app_folder='_local_cache',
      no_folders_no_save=True, DEBUG=False,
    )

  def setUp(self):
    """Reserve a unique test name; each data allocation is only twelve bytes."""
    self.name = 'ratio1_test_admission_' + uuid.uuid4().hex

  def _subject(self, create=True):
    """Create or attach the tiny production utility and register owned cleanup.

    Parameters
    ----------
    create : bool, optional
      Whether this subject owns the uniquely named allocation.

    Returns
    -------
    NumpySharedMemory
      Initialized production buffer with two six-element slots.
    """
    subject = NumpySharedMemory(
      mem_name=self.name, np_shape=(2, 3), np_type=np.uint8, create=create,
      is_buffer=True, maxlen=2, shape_offset=3, log=self.log,
    )
    self.addCleanup(subject.shutdown)
    self.assertTrue(subject.initialized, subject.error_message)
    return subject

  def test_plain_shapes_match_legacy_without_numpy_reduction(self):
    """Keep scalar/empty/layout/rank/count boundaries while avoiding np.prod."""
    subject = self._subject()
    arrays = [
      np.array(7, dtype=np.uint8), np.empty((0, 3), dtype=np.uint8),
      np.arange(6, dtype=np.uint8), np.arange(12, dtype=np.uint8)[::2],
      np.asfortranarray(np.arange(6, dtype=np.uint8).reshape(2, 3)),
      np.arange(7, dtype=np.uint8), np.ones((1, 1, 1, 1), dtype=np.uint8),
    ]
    for array in arrays:
      with self.subTest(shape=array.shape, strides=array.strides):
        expected = np.prod(array.shape) <= 6 and len(array.shape) <= 3
        with patch('naeural_core.utils.system_shared_memory.np.prod',
                   side_effect=AssertionError('plain ndarray must use size')):
          self.assertEqual(subject.check_data(array), expected)
        self.assertFalse(subject._np_mem_additional.any())

  def test_subclass_keeps_shape_reduction_and_ignores_custom_size(self):
    """A subclass can expose a shape unlike storage and an effectful size."""
    class CustomArray(np.ndarray):
      """Expose a deliberately oversized shape with forbidden size access."""

      @property
      def shape(self):
        """Return the legacy validation shape, not the underlying storage."""
        return (7,)

      @property
      def size(self):
        """Fail if validation accidentally changes subclass dispatch."""
        raise AssertionError('subclass size must not be inspected')

    subject = self._subject()
    array = np.arange(2, dtype=np.uint8).view(CustomArray)
    with patch('naeural_core.utils.system_shared_memory.np.prod',
               wraps=np.prod) as reduction:
      self.assertFalse(subject.check_data(array))
    reduction.assert_called_once_with((7,))
    self.assertFalse(subject._np_mem_additional.any())

  def test_nonarray_warning_and_shape_error_remain_legacy(self):
    """Nonarrays still warn before shape evaluation; no silent coercion occurs."""
    subject = self._subject()
    for invalid in (None, [], object()):
      with self.subTest(value=type(invalid).__name__):
        with patch.object(subject, '_add_warning') as warning:
          with self.assertRaises(AttributeError):
            subject.check_data(invalid)
        warning.assert_called_once_with('Data is not a numpy array')
    self.assertFalse(subject.check_data(SimpleNamespace(shape=(2,))))
    self.assertFalse(subject._np_mem_additional.any())

  def test_scalar_strided_and_fortran_roundtrips_release_exact_slot(self):
    """Exercise real creator/attacher storage without changing copy semantics."""
    owner = self._subject()
    reader = self._subject(create=False)
    arrays = [
      np.array(7, dtype=np.uint8), np.arange(12, dtype=np.uint8)[::2],
      np.asfortranarray(np.arange(6, dtype=np.uint8).reshape(2, 3)),
    ]
    for array in arrays:
      with self.subTest(shape=array.shape, strides=array.strides):
        slot = owner.add_data_to_buffer(array)
        self.assertEqual(slot, 0)
        self.assertEqual(owner._np_mem_additional[slot, 0], 1)
        np.testing.assert_array_equal(
          owner._np_mem_additional[slot, 1:1 + array.ndim], array.shape,
        )
        restored = reader.read_buffer_data(slot)
        np.testing.assert_array_equal(restored, array)
        self.assertEqual(restored.shape, array.shape)
        self.assertFalse(owner._np_mem_additional.any())
        del restored
    reader.shutdown()
    owner.shutdown()
    for suffix in ('', '_additional'):
      with self.assertRaises(FileNotFoundError):
        unexpected = shared_memory.SharedMemory(name=self.name + suffix)
        self.addCleanup(unexpected.close)

  def test_full_invalid_range_and_empty_admission_keep_control_semantics(self):
    """Admission failures and reads retain the existing slot release behavior."""
    owner = self._subject()
    data = np.arange(6, dtype=np.uint8)
    self.assertEqual(owner.add_data_to_buffer(data), 0)
    self.assertEqual(owner.add_data_to_buffer(data), 1)
    before = owner._np_mem_additional.copy()
    self.assertIsNone(owner.add_data_to_buffer(data))
    self.assertIsNone(owner.add_data_to_buffer(np.arange(7, dtype=np.uint8)))
    self.assertIsNone(owner.read_buffer_data(-1))
    self.assertIsNone(owner.read_buffer_data(2))
    np.testing.assert_array_equal(owner._np_mem_additional, before)
    owner.read_buffer_data(0)
    self.assertEqual(owner._np_mem_additional[1, 0], 1)
    owner.read_buffer_data(1)
    self.assertFalse(owner._np_mem_additional.any())
    self.assertIsNone(owner.read_buffer_data(1))

    # Empty arrays were already admitted. Shape restoration drops zero dimensions;
    # this shortcut does not redefine that legacy transport behavior.
    self.assertEqual(owner.add_data_to_buffer(np.empty((0,), dtype=np.uint8)), 0)
    owner.read_buffer_data(0)
    self.assertFalse(owner._np_mem_additional.any())


if __name__ == '__main__':
  unittest.main()
