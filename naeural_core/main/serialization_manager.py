"""
Runs the node's periodic state saves on one background thread.

The owner of a large state (the chain store, the network monitor) registers the save function it already
has and how often to run it. From then on the owner's thread only changes the state in memory; the manager's
thread calls the save function at that cadence. The files are written by the owner's own code, unchanged.

Why: a save ran on the thread that owns the state, which did nothing else for the whole write. Measured
under a 0.7 CPU limit, one chain-store save (5 MB) took its plugin thread 0.26 s on an idle node and 1.3 s on
a busy one, and one network-monitor save (20 MB) 1.5-1.9 s and 6.1-6.3 s. On this thread the owner no longer
runs the save. It can still wait briefly for the lock the save copies the state under and for the
interpreter while the save serializes: each save costs the same CPU and disk work as before.
"""
# One thread, one lock for the table of saves, one event to stop.
import threading
# A save that raises is logged with its full traceback: the owner is not there to see it.
import traceback
# Wall-clock seconds for the cadence of each save.
from time import time


class SerializationManager:
  """
  One background thread that calls registered save functions at their cadence.

  The Orchestrator starts it before any manager or plugin exists and stops it when the node closes.
  Owners reach it through ``SerializationManager.instance``.
  """

  # The node's manager, set by start(). It is None until then, so a save registered without a started
  # manager fails at once instead of being silently dropped.
  instance = None

  def __init__(self, log):
    """
    Prepare the manager; ``start`` is the way to create and run the node's one.

    Parameters
    ----------
    log : Logger
      The node logger. Only ``log.P`` is used, to report a save that raised.

    Returns
    -------
    None
    """
    # Keep the node logger: a save that raises is reported in the node log with its traceback.
    self.log = log
    # Registered saves: save function -> [seconds between runs, time of the next run].
    self._saves = {}
    # Owners register from their own threads while this thread reads the table.
    self._lock = threading.Lock()
    # Set by stop(); it also wakes the thread from its one-second wait.
    self._stopping = threading.Event()
    # A daemon thread: a node that is killed does not wait for it, and an orderly stop joins it in stop().
    self._thread = threading.Thread(target=self._run, name='serialization_manager', daemon=True)
    return

  @classmethod
  def start(cls, log):
    """
    Create the node's manager and start its thread.

    Parameters
    ----------
    log : Logger
      The node logger.

    Returns
    -------
    SerializationManager
      The running manager, also stored as ``SerializationManager.instance``.

    Examples
    --------
    >>> SerializationManager.start(self.log)   # Orchestrator.startup()
    """
    # Publish the manager before its thread runs, so owners can register as soon as start() returns.
    cls.instance = cls(log)
    # From here on registered saves run on this thread instead of on their owners' threads.
    cls.instance._thread.start()
    # Hand the running manager back; owners normally reach it through SerializationManager.instance.
    return cls.instance

  def register(self, save, every):
    """
    Run ``save()`` on the manager's thread every ``every`` seconds.

    The owner keeps changing its state on its own thread and no longer spends time saving it. Registering
    the same function again replaces its cadence.

    Parameters
    ----------
    save : callable
      The owner's save function, called with no arguments. It must be safe to call from another thread
      (take the owner's lock while it copies the state) and should return quickly when nothing changed.
    every : float
      Seconds between two runs, counted from the start of a run. The first run comes ``every`` seconds
      after the registration.

    Returns
    -------
    None

    Notes
    -----
    All saves share one thread, so a long save delays the others; their owners do not wait for it.

    Examples
    --------
    >>> SerializationManager.instance.register(self.netmon.network_save_status, every=120)
    """
    # Owners register from their own threads while the manager's thread reads the table.
    with self._lock:
      # The first run is due one cadence from now: the owner has just loaded or created its state.
      self._saves[save] = [every, time() + every]
    return

  def unregister(self, save):
    """
    Stop running a registered save, for an owner that is closing.

    Parameters
    ----------
    save : callable
      The function given to ``register``.

    Returns
    -------
    None

    Raises
    ------
    KeyError
      When ``save`` is not registered.

    Examples
    --------
    >>> SerializationManager.instance.unregister(self.__maybe_flush_chain_storage)   # chain store on_close
    """
    # Same lock as register(): the manager's thread may be reading the table right now.
    with self._lock:
      # Drop the entry so a closed owner is neither saved again nor kept alive by the table.
      del self._saves[save]
    return

  def stop(self, timeout):
    """
    Stop the thread, letting a save that is being written finish.

    Parameters
    ----------
    timeout : float
      Longest time in seconds to wait for a save that is in progress.

    Returns
    -------
    None

    Notes
    -----
    No extra save is made at stop: owners that need one do it in their own closing code, as before.

    Examples
    --------
    >>> SerializationManager.instance.stop(timeout=60)   # end of Orchestrator.close_main_loop()
    """
    # Ask the thread to end after the save it may be in, and wake it if it is waiting.
    self._stopping.set()
    # Wait for that save, bounded, so the node does not exit in the middle of a write.
    self._thread.join(timeout)
    return

  def _run(self):
    """
    Thread body: once a second, run the saves that are due, until ``stop``.

    Returns
    -------
    None
    """
    # Wake once a second (one cheap check) and leave as soon as stop() sets the event.
    while not self._stopping.wait(1):
      # Run what is due now; the owners' threads are not involved.
      self._run_due(time())
    return

  def _run_due(self, now):
    """
    Run every registered save whose time has come, one after another.

    Parameters
    ----------
    now : float
      Current time in seconds; saves whose next run is at or before it are run.

    Returns
    -------
    None

    Notes
    -----
    A save that raises is logged with its traceback and tried again at its next cadence; it does not
    stop this thread or the other saves.
    """
    # Hold the lock only to pick what is due, never during a save.
    with self._lock:
      # Copy the due entries under the lock; the saves themselves run outside it, so an owner can
      # register or unregister while a long save is being written.
      due = [(save, entry) for save, entry in self._saves.items() if entry[1] <= now]
    # One save at a time: the files are large and each save already competes for the interpreter.
    for save, entry in due:
      # Check again under the lock: during the previous save of this pass an owner may have closed and
      # unregistered, or stop() may have been called; neither must start another save.
      with self._lock:
        if self._stopping.is_set() or self._saves.get(save) is not entry:
          continue
        # Schedule the next run from the start of this one, so a slow save does not stretch the cadence.
        entry[1] = time() + entry[0]
      try:
        # The owner's own save function: it copies its state under its lock and writes its file as before.
        save()
      except Exception:
        self.log.P("Serialization manager: save {!r} failed:\n{}".format(save, traceback.format_exc()), color='r')
    return
