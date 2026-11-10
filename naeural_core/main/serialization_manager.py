"""
SerializationManager
====================

One small helper process, started by the Orchestrator and bound to its
lifecycle, that persists detached snapshots (chain-store replica, network
monitor database, epochs status, ...) so that plugin threads never write or
fsync files themselves.

How it works
------------
- ``submit(name, path, obj)`` only stores a reference (latest wins per
  ``name``) and returns at once. ``obj`` must be a detached snapshot: the
  caller must not change it afterwards.
- One feeder thread takes each pending snapshot in turn and streams its
  pickle to the worker in length-prefixed chunks. The interpreter lock is released on
  every chunk (1 MB), and no full copy of the pickle is held in memory.
- The worker is a stdlib-only script run with ``subprocess`` (not
  ``multiprocessing``, which would re-import the application in the child and
  cost hundreds of MB). It writes a temporary file, fsyncs it, renames it over
  the destination and fsyncs the folder, then acknowledges.
- If the worker is missing, dies or does not acknowledge, the same snapshot
  is written by the feeder thread itself, so a save is never lost because of
  the worker. A failed save is reported through ``on_error``.
- The worker exits when its input pipe closes, so it cannot outlive the node.

Set ``EE_SERIALIZATION_MANAGER=0`` to disable it (callers then save inline as
before) or ``EE_SERIALIZATION_MANAGER=thread`` to keep the feeder thread but
write from it without a worker process.
"""

import fcntl
import io
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time

__VER__ = '1.0.0'

ENV_SWITCH = 'EE_SERIALIZATION_MANAGER'

_END = struct.pack('>I', 0)
_ABORT = struct.pack('>I', 0xFFFFFFFF)

# Stdlib only. Started with `python -I -S -c`, so it never imports the application.
_WORKER_SOURCE = r'''
import os, signal, struct, sys

def _read(stream, size):
  data = stream.read(size)
  if len(data) != size:
    raise EOFError()
  return data

def main():
  try:
    os.nice(10)
    # A Ctrl-C meant for the node must not stop the worker before the last snapshots are written.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
  except Exception:
    pass
  inp, out = sys.stdin.buffer, sys.stdout.buffer
  while True:
    try:
      path = _read(inp, struct.unpack('>I', _read(inp, 4))[0]).decode('utf-8')
    except EOFError:
      return
    tmp, handle, error, size = path + '.sm-tmp', None, '', 0
    try:
      os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
      handle = open(tmp, 'wb')
    except Exception as exc:
      error = repr(exc)
    try:
      while True:
        length = struct.unpack('>I', _read(inp, 4))[0]
        if length == 0:
          break
        if length == 0xFFFFFFFF:
          error = error or 'aborted by sender'
          break
        chunk = _read(inp, length)
        if not error:
          try:
            handle.write(chunk)
            size += length
          except Exception as exc:
            error = repr(exc)
      if not error:
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp, path)
        folder = os.open(os.path.dirname(path) or '.', os.O_RDONLY)
        try:
          os.fsync(folder)
        finally:
          os.close(folder)
    except EOFError:
      error = 'eof'
    except Exception as exc:
      error = repr(exc)
    if handle is not None:
      try:
        handle.close()
      except Exception:
        pass
    if error:
      try:
        os.unlink(tmp)
      except Exception:
        pass
      if error == 'eof':
        return
    out.write(('ER ' + error.replace('\n', ' ') if error else 'OK ' + str(size)).encode('utf-8') + b'\n')
    out.flush()

main()
'''


# The pickler emits 64 KB frames. Each write to the pipe or file gives up the
# interpreter lock, and on a saturated interpreter getting it back takes tens of
# milliseconds, so frames are gathered (in C, by a BufferedWriter) into chunks
# of this size first.
CHUNK_BYTES = 1 << 20


class _FramedPipe(io.RawIOBase):
  """Raw sink that sends each buffer it is given as one length-prefixed chunk."""

  def __init__(self, pipe):
    super().__init__()
    self.pipe = pipe
    self.size = 0

  def writable(self):
    return True

  def write(self, data):
    length = len(data)
    if length and self.pipe is not None:
      # One write per chunk: every write gives up the interpreter lock once.
      self.pipe.write(struct.pack('>I', length) + bytes(data))
      self.size += length
    return length


def save_file_atomic(path, obj):
  """
  Pickle ``obj`` to ``path`` through a temporary file, fsync and rename.

  Parameters
  ----------
  path : str
    Destination file.
  obj : object
    Picklable snapshot.

  Returns
  -------
  int
    Number of bytes written.
  """
  folder = os.path.dirname(path) or '.'
  os.makedirs(folder, exist_ok=True)
  tmp = path + '.sm-tmp'
  try:
    with open(tmp, 'wb', buffering=CHUNK_BYTES) as stream:
      pickle.dump(obj, stream, protocol=pickle.HIGHEST_PROTOCOL)
      stream.flush()
      os.fsync(stream.fileno())
      size = stream.tell()
    os.replace(tmp, path)
    directory = os.open(folder, os.O_RDONLY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)
  finally:
    if os.path.exists(tmp):
      os.unlink(tmp)
  return size


class SerializationManager:
  """
  Latest-wins background saver with one worker process.

  Parameters
  ----------
  log : Logger, optional
    Used only for messages.
  use_process : bool, optional
    If False, the feeder thread writes the files itself (no worker process).
  interval : float, optional
    Minimum number of seconds between the end of one save and the start of
    the next save of the same name. The default 0 saves as soon as the feeder
    is free; callers are expected to pace themselves (the chain store submits
    at most every 10 s) and a newer snapshot always replaces an unsaved one.
  ack_timeout : float, optional
    Seconds to wait for the worker to acknowledge a file before it is killed
    and the file is written by the feeder thread.
  respawn_delay : float, optional
    Minimum number of seconds between two worker starts.
  """

  def __init__(self, log=None, use_process=True, interval=0.0, ack_timeout=120.0, respawn_delay=30.0):
    self.log = log
    self.use_process = use_process
    self.interval = interval
    self.ack_timeout = ack_timeout
    self.respawn_delay = respawn_delay
    self._cond = threading.Condition()
    self._pending = {}       # name -> [path, obj, on_error, due]
    self._last_done = {}     # name -> time of the last finished save
    self._busy = None        # name being saved now
    self._stop = False
    self._closed = False     # set by the feeder thread when it will not take another snapshot
    self._flush = 0          # number of flush() calls waiting
    self._proc = None
    self._last_spawn = 0
    self._thread = None
    self._stats = {
      'saves': 0, 'bytes': 0, 'errors': 0, 'inline': 0, 'superseded': 0,
      'worker_starts': 0, 'last_ms': 0.0, 'max_ms': 0.0, 'last_error': None,
    }
    return

  def P(self, msg, color=None):
    msg = "[SERMGR] " + msg
    if self.log is not None:
      self.log.P(msg, color=color)
    else:
      print(msg, flush=True)
    return

  # lifecycle

  def start(self):
    """Start the feeder thread (the worker process starts with the first save)."""
    if self._thread is None:
      self._thread = threading.Thread(target=self._run, name='serialization_manager', daemon=True)
      self._thread.start()
      self.P("SerializationManager v{} started (worker process: {}, interval: {}s)".format(
        __VER__, self.use_process, self.interval
      ))
    return self

  def stop(self, timeout=60.0):
    """
    Save everything still pending, then stop the feeder thread and the worker.

    Parameters
    ----------
    timeout : float, optional
      Seconds to wait for the pending saves.

    Returns
    -------
    bool
      True if every pending snapshot was saved before the timeout.
    """
    with self._cond:
      self._stop = True
      self._cond.notify_all()
    thread = self._thread
    if thread is not None:
      thread.join(timeout=timeout)
    done = thread is None or not thread.is_alive()
    if not done:
      self.P("Pending saves did not finish in {}s; stopping the worker.".format(timeout), color='r')
    self._kill_worker()
    return done

  def flush(self, timeout=60.0, name=None):
    """
    Wait until the snapshots submitted so far are on disk.

    Parameters
    ----------
    timeout : float, optional
      Seconds to wait.
    name : str, optional
      Wait only for this name. Default waits for all names.

    Returns
    -------
    bool
      True if nothing submitted before the call is still unsaved.
    """
    deadline = time.time() + timeout
    with self._cond:
      self._flush += 1
      self._cond.notify_all()
      while True:
        waiting = (name in self._pending or self._busy == name) if name else (self._pending or self._busy)
        if not waiting:
          break
        remaining = deadline - time.time()
        if remaining <= 0 or self._thread is None or not self._thread.is_alive():
          break
        self._cond.wait(timeout=min(remaining, 0.5))
      self._flush -= 1
    return not waiting

  # public API

  def submit(self, name, path, obj, on_error=None):
    """
    Queue a detached snapshot for saving. A newer snapshot replaces an unsaved one.

    Parameters
    ----------
    name : str
      Identity of the saved thing (one pending snapshot is kept per name).
    path : str
      Destination file.
    obj : object
      Detached picklable snapshot; the caller must not change it afterwards.
    on_error : callable, optional
      Called with the error text if the save fails (from the feeder thread).

    Returns
    -------
    bool
      False once the manager has stopped; the caller must then save by itself.
    """
    with self._cond:
      # Accepted until the feeder thread has finished, also while stop() is draining:
      # a caller that is refused saves by itself, and must then be the only writer.
      if self._closed or self._thread is None or not self._thread.is_alive():
        return False
      item = self._pending.get(name)
      if item is None:
        due = self._last_done.get(name, 0) + self.interval
        self._pending[name] = [path, obj, on_error, due]
      else:
        item[0], item[1], item[2] = path, obj, on_error
        self._stats['superseded'] += 1
      self._cond.notify_all()
    return True

  def get_stats(self):
    """Return a copy of the counters (saves, bytes, errors, timings, pending)."""
    with self._cond:
      stats = dict(self._stats)
      stats['pending'] = len(self._pending)
    stats['worker_alive'] = self._proc is not None and self._proc.poll() is None
    return stats

  # feeder thread

  def _run(self):
    while True:
      with self._cond:
        while True:
          now = time.time()
          immediate = self._stop or self._flush
          name = min(self._pending, key=lambda key: self._pending[key][3], default=None)
          if name is not None and (immediate or self._pending[name][3] <= now):
            break
          if name is None and self._stop:
            break
          self._cond.wait(timeout=None if name is None else max(0.01, self._pending[name][3] - now))
        if name is None:
          self._closed = True
          break
        path, obj, on_error, _ = self._pending.pop(name)
        self._busy = name
      try:
        self._save(path, obj, on_error)
      except Exception as exc:  # never let the feeder die
        self.P("Unexpected save error for {}: {}".format(path, exc), color='r')
      finally:
        with self._cond:
          self._last_done[name] = time.time()
          self._busy = None
          self._cond.notify_all()
    return

  def _save(self, path, obj, on_error):
    start = time.time()
    saved, size, error = False, 0, None
    if self.use_process:
      saved, size, error = self._save_with_worker(path, obj)
    if not saved:
      try:
        size = save_file_atomic(path, obj)
        saved = True
        if self.use_process:
          self._stats['inline'] += 1
      except Exception as exc:
        error = "{} / inline: {!r}".format(error, exc)
    elapsed = (time.time() - start) * 1000
    with self._cond:
      self._stats['last_ms'] = elapsed
      self._stats['max_ms'] = max(self._stats['max_ms'], elapsed)
      if saved:
        self._stats['saves'] += 1
        self._stats['bytes'] += size
      else:
        self._stats['errors'] += 1
        self._stats['last_error'] = error
    if not saved:
      self.P("Save failed for {}: {}".format(path, error), color='r')
      if on_error is not None:
        try:
          on_error(error)
        except Exception:
          pass
    return

  # worker process

  def _ensure_worker(self):
    if self._proc is not None and self._proc.poll() is None:
      return True
    if time.time() - self._last_spawn < self.respawn_delay:
      return False
    self._kill_worker()
    self._last_spawn = time.time()
    # No preexec_fn and no multiprocessing: Popen execs a fresh interpreter,
    # which is safe from a process with many threads.
    self._proc = subprocess.Popen(
      [sys.executable, '-I', '-S', '-c', _WORKER_SOURCE],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
      close_fds=True, start_new_session=True,
    )
    try:
      # A pipe as large as one chunk lets the feeder go on pickling while the worker writes.
      fcntl.fcntl(self._proc.stdin.fileno(), getattr(fcntl, 'F_SETPIPE_SZ', 1031), CHUNK_BYTES)
    except Exception:
      pass
    self._stats['worker_starts'] += 1
    self.P("Serialization worker started (pid {}).".format(self._proc.pid))
    return True

  def _kill_worker(self):
    proc, self._proc = self._proc, None
    if proc is None:
      return
    for closer in (proc.stdin.close, proc.stdout.close):
      try:
        closer()
      except Exception:
        pass
    try:
      proc.wait(timeout=5)
    except Exception:
      try:
        proc.kill()
        proc.wait(timeout=5)
      except Exception:
        pass
    return

  def _read_ack(self):
    ready, _, _ = select.select([self._proc.stdout], [], [], self.ack_timeout)
    if not ready:
      raise TimeoutError("worker did not acknowledge in {}s".format(self.ack_timeout))
    line = self._proc.stdout.readline()
    if not line:
      raise EOFError("worker exited")
    return line.decode('utf-8', errors='replace').strip()

  def _save_with_worker(self, path, obj):
    try:
      if not self._ensure_worker():
        return False, 0, 'worker unavailable'
      pipe = self._proc.stdin
      encoded = path.encode('utf-8')
      pipe.write(struct.pack('>I', len(encoded)) + encoded)
      framed = _FramedPipe(pipe)
      sink = io.BufferedWriter(framed, buffer_size=CHUNK_BYTES)
      try:
        try:
          pickle.dump(obj, sink, protocol=pickle.HIGHEST_PROTOCOL)
          sink.flush()
        finally:
          # Whatever is still buffered after a failure must never reach the pipe later.
          framed.pipe = None
        pipe.write(_END)
      except OSError:
        raise
      except Exception as exc:
        # The object could not be pickled: tell the worker to drop the partial file.
        pipe.write(_ABORT)
        pipe.flush()
        self._read_ack()
        return False, 0, "pickle: {!r}".format(exc)
      pipe.flush()
      ack = self._read_ack()
      if ack.startswith('OK'):
        return True, framed.size, None
      return False, 0, ack
    except Exception as exc:
      self._kill_worker()
      return False, 0, "worker: {!r}".format(exc)


_GLOBAL = None


def get_serialization_manager():
  """Return the process-wide manager started by the Orchestrator, or None."""
  return _GLOBAL


def start_global(log=None):
  """
  Start the process-wide manager unless it is disabled by the environment.

  Returns
  -------
  SerializationManager or None
  """
  global _GLOBAL
  mode = str(os.environ.get(ENV_SWITCH, '1')).strip().lower()
  if _GLOBAL is None and mode not in ('0', 'false', 'no', 'off'):
    _GLOBAL = SerializationManager(log=log, use_process=mode != 'thread').start()
  return _GLOBAL


def stop_global(timeout=60.0):
  """Flush and stop the process-wide manager, if any."""
  global _GLOBAL
  manager = _GLOBAL
  if manager is not None:
    # Stays reachable while it drains, so a late snapshot is queued behind the
    # pending ones instead of being written inline at the same time.
    manager.stop(timeout=timeout)
  _GLOBAL = None
  return


def save_pickle_to_data(log, data, fn, subfolder_path=None, **kwargs):
  """
  Drop-in replacement for ``log.save_pickle_to_data`` that saves in the background.

  The snapshot is queued on the process-wide manager when one is running and
  the file is a plain pickle; otherwise the logger saves it as before.

  Parameters
  ----------
  log : Logger
    Logger that owns the data folder.
  data : object
    Detached picklable snapshot; the caller must not change it afterwards.
  fn : str
    File name inside the data folder.
  subfolder_path : str, optional
    Sub-folder of the data folder.
  **kwargs
    Passed to ``log.save_pickle_to_data`` when saving inline.

  Returns
  -------
  object
    True when queued, otherwise the logger's return value.
  """
  manager = get_serialization_manager()
  if manager is not None and not kwargs.get('compressed') and '.pklz' not in fn:
    path = os.path.join(log.get_data_folder(), (subfolder_path or '').lstrip('/'), fn)
    if manager.submit(path, path, data):
      return True
  return log.save_pickle_to_data(data=data, fn=fn, subfolder_path=subfolder_path, **kwargs)
