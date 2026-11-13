"""
Runs the node's periodic state saves, each one on a background thread of its own.

The owner of a large state (the chain store, the network monitor) registers the save function it already
has and how often to run it. From then on the owner's thread only changes the state in memory; the thread
the manager started for that save calls the save function at that cadence. The files are written by the
owner's own code, unchanged.

Why the owner does not save: a save ran on the thread that owns the state, which did nothing else for the
whole write. Measured under a 0.7 CPU limit, one chain-store save (5 MB) took its plugin thread 0.26 s on
an idle node and 1.3 s on a busy one, and one network-monitor save (20 MB) 1.5-1.9 s and 6.1-6.3 s. The
owner no longer runs the save. It can still wait briefly for the lock the save copies the state under and
for the interpreter while the save serializes: each save costs the same CPU and disk work as before.

Why one thread per save and not one thread for all saves: on a shared thread, a chain-store save that
became due while the network-monitor save was being written would wait for it, so an accepted chain-store
write would reach the disk seconds later. With a thread each, the chain-store save does not wait for the
network-monitor save. They still compete for the interpreter and the disk when both write at the same time.

Every ``STATS_INTERVAL`` seconds each thread logs one ``SERIALIZATION_STATS`` line with how many times its
save ran and how long the runs took in total and at most; this is how the cost of each save is read from a
node log.
"""
# One thread per save, one lock for the table of saves, one event to stop them all.
import threading
# A save that raises is logged with its full traceback: the owner is not there to see it.
import traceback
# Monotonic seconds for the cadence of each save: with the wall clock, a clock stepped back (NTP or a manual
# change) would hold every save back for the size of the step.
from time import monotonic


class SerializationManager:
  """
  Calls registered save functions at their cadence, each one on a background thread of its own.

  The Orchestrator creates it before any manager or plugin exists and stops it when the node closes.
  Owners reach it through ``SerializationManager.instance``. A save's thread ends when the save is
  unregistered or registered again, or when the manager stops.
  """

  # The node's manager, set by start(). It is None until then, so a save registered without a started
  # manager fails at once instead of being silently dropped.
  instance = None

  # New: seconds between two statistics lines of one save, so the cost of each save can be read from the
  # node log without a line for every run.
  STATS_INTERVAL = 300

  def __init__(self, log):
    """
    Prepare the manager; ``start`` is the way to create the node's one.

    Parameters
    ----------
    log : Logger
      The node logger. Only ``log.P`` is used, to report a save that raised and the statistics of each save.

    Returns
    -------
    None

    Examples
    --------
    >>> manager = SerializationManager(log)   # start() does this and publishes it as the instance
    >>> manager._saves, manager._threads
    ({}, [])
    """
    # Keep the node logger: a save that raises is reported in the node log with its traceback.
    self.log = log
    # Registered saves: save function -> [seconds between runs, time of the next run]. A save's thread runs
    # it only while the entry it was started with is the one in this table.
    self._saves = {}
    # Owners register from their own threads while the saves' threads read the table.
    self._lock = threading.Lock()
    # Set by stop(); it also wakes every save's thread from its one-second wait.
    self._stopping = threading.Event()
    # The threads register() started, one per registration, so stop() can wait for each; a register() that
    # starts a thread also drops the ones that have ended. The list is replaced, never changed in place.
    self._threads = []
    return

  @classmethod
  def start(cls, log):
    """
    Create the node's manager.

    Parameters
    ----------
    log : Logger
      The node logger.

    Returns
    -------
    SerializationManager
      The manager, also stored as ``SerializationManager.instance``.

    Examples
    --------
    >>> SerializationManager.start(self.log)   # Orchestrator.startup()
    """
    # Publish the manager, so owners can register as soon as start() returns. No thread starts here:
    # register() starts the thread of each save.
    cls.instance = cls(log)
    # Hand the running manager back; owners normally reach it through SerializationManager.instance.
    return cls.instance

  def register(self, save, every):
    """
    Run ``save()`` every ``every`` seconds on a thread started for this save alone.

    The owner keeps changing its state on its own thread and no longer spends time saving it. Registering
    the same function again replaces its cadence: the thread of the earlier registration ends at its next
    check and a new thread starts.

    Parameters
    ----------
    save : function or bound method
      The owner's save function, called with no arguments. It must be safe to call from another thread
      (take the owner's lock while it copies the state) and should return quickly when nothing changed.
      Its ``__qualname__`` names the thread and the statistics line, so anything else fails here.
    every : int or float
      Seconds between two runs, counted from the start of a run; a positive, finite number (a bool is
      refused). The first run comes ``every`` seconds after the registration.

    Returns
    -------
    None

    Raises
    ------
    ValueError
      When ``every`` is not a positive, finite int or float: 0, a negative number, NaN, infinity, a bool,
      None or a string.
    AttributeError
      When ``save`` has no ``__qualname__``.
    RuntimeError
      When the save's thread cannot be started.

    Notes
    -----
    When this raises nothing has changed: no thread was started and an earlier registration of ``save``
    keeps running.

    Each save has its own thread, so a long save does not delay another one; their owners do not wait for
    it either. The saves still compete for the interpreter and the disk. The cadence is counted on the
    monotonic clock, so a change of the wall clock does not move it. The thread checks once a second, so a
    cadence below one second runs the save about once a second.

    Examples
    --------
    >>> SerializationManager.instance.register(self.netmon.network_save_status, every=120)
    >>> SerializationManager.instance.register(self.netmon.network_save_status, every=0)
    Traceback (most recent call last):
    ValueError: every must be a positive, finite number of seconds, got 0 for <bound method ...>.
    """
    # New: refuse a cadence that is not a positive, finite number. With 0 or less the save would be due again at
    # every check of its thread, so a 20 MB save would run back to back; with NaN or infinity no run is ever
    # due, so the save would silently never run. A bool is refused too, as the chain store refuses one for its
    # timeout.
    if isinstance(every, bool) or not isinstance(every, (int, float)) or not 0 < every < float("inf"):
      # Loudly and before anything changes, like a save without __qualname__: the owner's start fails with
      # the cadence it computed.
      raise ValueError("every must be a positive, finite number of seconds, got {!r} for {!r}.".format(every, save))
    # A new entry at every registration, which the save's thread compares with the table: the first run is
    # due one cadence from now, on the monotonic clock (the owner has just loaded or created its state).
    entry = [every, monotonic() + every]
    # Before, each owner ran its save on its own plugin thread. Now each save gets a daemon thread of its own:
    # the owner's thread does not spend the write, and a chain-store save that becomes due does not wait while
    # the network-monitor save is being written, as it would on one thread shared by all saves. A killed node
    # does not wait for the thread, stop() does. The thread is named after the save; anything without a
    # __qualname__ fails here, before the table changes.
    thread = threading.Thread(
      target=self._run, args=(save, entry), name='serialization_manager:' + save.__qualname__, daemon=True
    )
    # Owners register from their own threads while the saves' threads read the table.
    with self._lock:
      # Started first and under the lock: when a thread cannot be started this raises with the table as it
      # was, so an earlier registration of this save keeps running, and the list stop() reads never holds a
      # thread that has not started. The new thread's first check waits for this lock.
      thread.start()
      # Store the entry of the running thread. A thread of an earlier registration of this save finds another
      # entry at its next check and ends.
      self._saves[save] = entry
      # Keep the thread for stop() and drop the ones that ended, so the list does not grow when owners restart.
      # A new list each time: stop() reads it without the lock.
      self._threads = [running for running in self._threads if running.is_alive()] + [thread]
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

    Notes
    -----
    This does not wait for the save's thread, which ends at its next check. A save that the thread had
    already picked (it passed the check under the table lock just before) can still run once after
    ``unregister`` returned; it is not picked again. An owner whose successor writes the same file must
    order the two saves itself: the chain store does it with one save lock for the process, so that late
    save cannot land after a newer save of a restarted instance.

    Examples
    --------
    >>> SerializationManager.instance.unregister(self.__maybe_flush_chain_storage)   # chain store on_close
    """
    # Same lock as register(): the save's thread may be reading the table right now.
    with self._lock:
      # Drop the entry: the save's thread ends at its next check and the table no longer keeps the closed owner
      # alive. A save the thread already picked can still run once after this returns (see Notes).
      del self._saves[save]
    return

  def stop(self, timeout):
    """
    Stop every save's thread, letting the saves that are being written finish.

    Parameters
    ----------
    timeout : float
      Longest time in seconds to wait, for all the saves in progress together.

    Returns
    -------
    None

    Notes
    -----
    No thread picks a save once stop was asked. A save picked just before (its thread passed the check
    under the table lock) still runs, and this method waits for it within the timeout. No extra save is
    made at stop: owners that need one do it in their own closing code, as before.

    Examples
    --------
    >>> SerializationManager.instance.stop(timeout=60)   # end of Orchestrator.close_main_loop()
    """
    # Ask every thread to end after the save it may be in, and wake those that are waiting.
    self._stopping.set()
    # One thread per save, and the timeout bounds the whole stop, so all of them are waited for until one
    # deadline on the monotonic clock.
    deadline = monotonic() + timeout
    # Every thread register() started; one that has ended returns at once.
    for thread in self._threads:
      # Wait for its save, so the node does not exit in the middle of a write; a join whose time is already
      # up returns at once.
      thread.join(deadline - monotonic())
    return

  def _run(self, save, entry):
    """
    Thread body of one save: once a second, run it when it is due, until this registration of it ends.

    The registration ends when the save is unregistered or registered again. The current time is read from
    the monotonic clock, the clock ``register`` schedules on. The thread also ends when ``stop`` is called.

    Parameters
    ----------
    save : function or bound method
      The save this thread runs.
    entry : list
      The entry ``register`` stored for this thread, ``[seconds between runs, time of the next run]``.

    Returns
    -------
    None

    Examples
    --------
    Never called directly: ``register`` starts it as the save's thread.

    >>> thread = threading.Thread(
    ...   target=self._run, args=(save, entry), name='serialization_manager:' + save.__qualname__, daemon=True
    ... )
    >>> thread.start()   # under self._lock, in register()
    """
    # New: this save's statistics since its last line, kept by this thread alone: when the counting began,
    # the number of runs, and their total and longest duration in seconds.
    stats = [monotonic(), 0, 0.0, 0.0]
    # Wake once a second (one cheap check) and leave as soon as stop() sets the event.
    while not self._stopping.wait(1):
      # Run only this thread's save, on the monotonic clock so a wall clock stepped back does not hold it back;
      # the thread ends once this registration of the save is gone (unregistered or registered again) or
      # stop() was called.
      if not self._run_due(save, entry, monotonic(), stats):
        return
    return

  def _run_due(self, save, entry, now, stats):
    """
    Run one save if its time has come, and log its statistics line when the interval has passed.

    Parameters
    ----------
    save : function or bound method
      The save of the calling thread.
    entry : list
      The entry the calling thread was started with, ``[seconds between runs, time of the next run]``.
    now : float
      Current monotonic time in seconds (``time.monotonic()``); the save runs when its next run is at or
      before it.
    stats : list
      The calling thread's statistics, ``[start of counting, runs, total seconds, longest seconds]``,
      updated here.

    Returns
    -------
    bool
      False when the calling thread must end: ``save`` is no longer registered with ``entry`` (it was
      unregistered or registered again) or ``stop`` was asked. True otherwise.

    Notes
    -----
    A save that raises is logged with its traceback and tried again at its next cadence; it does not
    stop this thread or the other saves.

    Examples
    --------
    >>> self._run_due(save, entry, monotonic(), stats)   # in _run(), once a second
    True
    >>> SerializationManager.instance.unregister(save)
    >>> self._run_due(save, entry, monotonic(), stats)   # the next check: the thread ends
    False
    """
    # Hold the lock only to check and schedule, never during the save, so an owner can register or
    # unregister while a long save is being written.
    with self._lock:
      if self._stopping.is_set() or self._saves.get(save) is not entry:
        # The thread ends: stop() was called, or the owner closed, or it registered again and that registration
        # has its own thread; in none of these cases may this thread start another save.
        return False
      # This thread's own check, on the time it was given: the save is due when its next run is at or before now.
      due = entry[1] <= now
      if due:
        # Schedule the next run from the start of this one, so a slow save does not stretch the cadence; on
        # the monotonic clock, like register() and _run().
        entry[1] = now + entry[0]
    if due:
      # New: time the save on the monotonic clock for the statistics line.
      started = monotonic()
      try:
        # The owner's own save function: it copies its state under its lock and writes its file as before.
        save()
      except Exception:
        self.log.P("Serialization manager: save {!r} failed:\n{}".format(save, traceback.format_exc()), color='r')
      # New: one more run, its duration added to the total and compared with the longest.
      took = monotonic() - started
      # New: count it in this thread's statistics.
      stats[1:] = [stats[1] + 1, stats[2] + took, max(stats[3], took)]
    if now - stats[0] >= self.STATS_INTERVAL:
      self.log.P("SERIALIZATION_STATS save={} period_s={:.0f} calls={} total_s={:.3f} max_s={:.3f}".format(
        save.__qualname__, now - stats[0], *stats[1:]
      ))
      # New: count again from now, for the next line.
      stats[:] = [now, 0, 0.0, 0.0]
    # The save is still registered: the thread keeps running.
    return True
