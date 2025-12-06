"""
Semaphored Paired Plugin Mixin

This mixin provides semaphore-based synchronization between paired plugins,
enabling coordination between native plugins (providers) and Container App
Runners (consumers).

Use Cases:
  - Keysoft: Jeeves FastAPI (native) + CAR container
  - RedMesh: Pentester API (native) + CAR UI container
  - CerviGuard: Local Serving API (native) + WAR container

Provider plugins (native) use:
  - semaphore_set_env(): Set environment variables for paired plugins
  - semaphore_set_ready(): Signal initialization complete
  - semaphore_clear(): Clean up on shutdown

Consumer plugins (CAR/WAR) use:
  - semaphore_is_ready(): Check if all dependencies are ready
  - semaphore_get_env(): Collect environment variables from dependencies
  - semaphore_get_missing(): Get list of missing semaphores

Configuration:
  Provider: SEMAPHORE = "UNIQUE_KEY"
  Consumer: SEMAPHORED_KEYS = ["KEY1", "KEY2"]
"""

from time import time as tm


class _SemaphoredPairedPluginMixin(object):
  """
  Mixin for coordinating startup and environment exchange between paired plugins
  (e.g., a native plugin and a Container App Runner) using shared memory semaphores.
  """

  def __init__(self):
    self.__semaphore_wait_start = None
    self.__semaphore_ready_logged = set()
    super(_SemaphoredPairedPluginMixin, self).__init__()
    return

  # ============================================================================
  # Provider Methods (for native plugins that signal readiness)
  # ============================================================================

  def _semaphore_ensure_structure(self):
    """Ensure the semaphore data structure exists in shared memory."""
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return None

    if semaphore_key not in self.plugins_shmem:
      self.plugins_shmem[semaphore_key] = {
        'start': False,
        'env': {},
        'metadata': {
          'instance_id': self.cfg_instance_id,
          'plugin_signature': self.__class__.__name__,
          'ready_timestamp': None,
        }
      }
    return self.plugins_shmem[semaphore_key]

  def semaphore_set_ready(self):
    """
    Signal that this plugin is ready.
    Sets 'start' = True in the shared memory segment identified by cfg_semaphore.

    Returns
    -------
    bool
      True if semaphore was set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    semaphore_data = self._semaphore_ensure_structure()
    semaphore_data['start'] = True
    semaphore_data['metadata']['ready_timestamp'] = tm()

    return True

  def semaphore_set_env(self, key, value, use_prefix=True):
    """
    Set an environment variable to be shared with the paired plugin.

    By default, the key is prefixed with the semaphore name to avoid collisions.
    For example: semaphore_set_env("PORT", 5080) with SEMAPHORE="JEEVES"
    results in env var "JEEVES_PORT=5080".

    Set use_prefix=False to use the key as-is without prefixing.

    Parameters
    ----------
    key : str
      The environment variable name (prefixed with semaphore key if use_prefix=True)
    value : any
      The environment variable value (will be converted to string)
    use_prefix : bool
      If True (default), prefix key with semaphore name. If False, use key as-is.

    Returns
    -------
    bool
      True if env var was set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    semaphore_data = self._semaphore_ensure_structure()

    # Prefix the key with semaphore name for namespacing (unless disabled)
    if use_prefix:
      full_key = "{}_{}".format(semaphore_key, key)
    else:
      full_key = key
    semaphore_data['env'][full_key] = str(value)
    return True

  def semaphore_set_env_dict(self, env_dict, use_prefix=True):
    """
    Set multiple environment variables at once.

    Parameters
    ----------
    env_dict : dict
      Dictionary of {key: value} pairs.
    use_prefix : bool
      If True (default), prefix keys with semaphore name. If False, use keys as-is.

    Returns
    -------
    bool
      True if all env vars were set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    for key, value in env_dict.items():
      self.semaphore_set_env(key, value, use_prefix=use_prefix)
    return True

  def semaphore_clear(self):
    """
    Clear the semaphore (e.g., on plugin shutdown).

    This signals to waiting plugins that this dependency is no longer available.
    Should be called in on_close() of provider plugins.
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return

    if semaphore_key in self.plugins_shmem:
      self.plugins_shmem[semaphore_key]['start'] = False
      self.plugins_shmem[semaphore_key]['metadata']['ready_timestamp'] = None
    return

  # ============================================================================
  # Consumer Methods (for CAR/WAR plugins that wait for dependencies)
  # ============================================================================

  def _semaphore_get_keys(self):
    """Get the list of semaphore keys this plugin waits for."""
    keys = getattr(self, 'cfg_semaphored_keys', None)
    return keys if keys else []

  def semaphore_is_ready(self, semaphore_key=None):
    """
    Check if a specific semaphore or all required semaphores are ready.

    Parameters
    ----------
    semaphore_key : str, optional
      Specific semaphore to check. If None, checks all SEMAPHORED_KEYS.

    Returns
    -------
    bool
      True if ready, False otherwise
    """
    if semaphore_key:
      # Check specific semaphore
      shmem_data = self.plugins_shmem.get(semaphore_key, {})
      return shmem_data.get('start', False)

    # Check all required semaphores
    required_keys = self._semaphore_get_keys()
    if not required_keys:
      return True  # No dependencies, always ready

    for key in required_keys:
      shmem_data = self.plugins_shmem.get(key, {})
      if not shmem_data.get('start', False):
        return False

    return True

  def semaphore_get_env(self):
    """
    Retrieve and aggregate environment variables from all semaphored keys.

    Returns
    -------
    dict
      Merged dictionary of all environment variables from ready semaphores
    """
    required_keys = self._semaphore_get_keys()
    if not required_keys:
      return {}

    result = {}
    for key in required_keys:
      shmem_data = self.plugins_shmem.get(key, {})
      if shmem_data.get('start', False):
        env_vars = shmem_data.get('env', {})
        result.update(env_vars)

    return result

  def semaphore_get_missing(self):
    """
    Get list of semaphores that are not yet ready.

    Returns
    -------
    list
      List of semaphore keys that are not ready
    """
    missing = []
    for key in self._semaphore_get_keys():
      if not self.semaphore_is_ready(key):
        missing.append(key)
    return missing

  def semaphore_get_status(self):
    """
    Get detailed status of all required semaphores.

    Returns
    -------
    dict
      Status information for each required semaphore
    """
    status = {}
    for key in self._semaphore_get_keys():
      shmem_data = self.plugins_shmem.get(key, {})
      if shmem_data:
        metadata = shmem_data.get('metadata', {})
        status[key] = {
          'ready': shmem_data.get('start', False),
          'env_count': len(shmem_data.get('env', {})),
          'provider': metadata.get('plugin_signature'),
          'ready_since': metadata.get('ready_timestamp'),
        }
      else:
        status[key] = {
          'ready': False,
          'env_count': 0,
          'provider': None,
          'ready_since': None,
        }
    return status

  def semaphore_start_wait(self):
    """
    Mark the start of semaphore waiting period.

    Call this when beginning to wait for semaphores.
    """
    if self.__semaphore_wait_start is None:
      self.__semaphore_wait_start = tm()
    return

  def semaphore_get_wait_elapsed(self):
    """
    Get elapsed time since waiting started.

    Returns
    -------
    float
      Elapsed time in seconds, or 0 if not waiting
    """
    if self.__semaphore_wait_start is None:
      return 0
    return tm() - self.__semaphore_wait_start

  def semaphore_reset_wait(self):
    """Reset the semaphore wait state (e.g., for retry after restart)."""
    self.__semaphore_wait_start = None
    self.__semaphore_ready_logged.clear()
    return

  def semaphore_check_with_logging(self):
    """
    Check semaphore status and log appropriately.

    Logs when individual semaphores become ready (only once per semaphore).

    Returns
    -------
    bool
      True if all semaphores are ready, False otherwise
    """
    required_keys = self._semaphore_get_keys()
    if not required_keys:
      return True

    all_ready = True
    for key in required_keys:
      is_ready = self.semaphore_is_ready(key)
      if is_ready and key not in self.__semaphore_ready_logged:
        self.__semaphore_ready_logged.add(key)
      elif not is_ready:
        all_ready = False

    return all_ready
