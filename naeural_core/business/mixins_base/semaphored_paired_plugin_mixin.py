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
    """
    Initialize all semaphore-related state variables.

    Consumer state (for plugins waiting on dependencies):
      - __semaphore_wait_start: Timestamp when waiting began
      - __semaphore_ready_logged: Set of semaphores already logged as ready

    Provider state (for plugins signaling readiness):
      - _semaphore_signaled: Flag to prevent duplicate signaling
    """
    # Consumer state (existing - for plugins that wait for others)
    self.__semaphore_wait_start = None
    self.__semaphore_ready_logged = set()

    # Provider state (new - for plugins that signal they're ready)
    self._semaphore_signaled = False

    super(_SemaphoredPairedPluginMixin, self).__init__()
    return

  def _semaphore_Pd(self, msg):
    """Safe debug logging - only logs if Pd method is available."""
    if hasattr(self, 'Pd') and callable(self.Pd):
      self.Pd(msg)
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
        'is_ready': False,
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
    Sets 'is_ready' = True in the shared memory segment identified by cfg_semaphore.

    Returns
    -------
    bool
      True if semaphore was set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    semaphore_data = self._semaphore_ensure_structure()
    semaphore_data['is_ready'] = True
    semaphore_data['metadata']['ready_timestamp'] = tm()
    self._semaphore_Pd("Semaphore '{}' set to READY".format(semaphore_key))
    return True

  def semaphore_set_env(self, key, value):
    """
    Set an environment variable to be shared with the paired plugin.

    The key is stored twice: once prefixed with the semaphore name for namespacing,
    and once without prefix for consumers that expect the raw key.

    Parameters
    ----------
    key : str
      The environment variable name (stored as both prefixed and raw)
    value : any
      The environment variable value (will be converted to string)

    Returns
    -------
    bool
      True if env var was set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    semaphore_data = self._semaphore_ensure_structure()

    # Store both prefixed and raw variants for flexibility
    value_str = str(value)
    prefixed_key = "{}_{}".format(semaphore_key, key)
    semaphore_data['env'][prefixed_key] = value_str
    semaphore_data['env'][key] = value_str
    self._semaphore_Pd(
      "Semaphore '{}' env vars set: {} / {} = {}".format(
        semaphore_key, prefixed_key, key, value))
    return True

  def semaphore_set_env_dict(self, env_dict):
    """
    Set multiple environment variables at once.

    Each key is stored with both prefixed and raw variants, matching
    semaphore_set_env() behavior.

    Parameters
    ----------
    env_dict : dict
      Dictionary of {key: value} pairs.

    Returns
    -------
    bool
      True if all env vars were set, False if SEMAPHORE not configured
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return False

    for key, value in env_dict.items():
      self.semaphore_set_env(key, value)
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
      self.plugins_shmem[semaphore_key]['is_ready'] = False
      self.plugins_shmem[semaphore_key]['metadata']['ready_timestamp'] = None
      self._semaphore_Pd("Semaphore '{}' cleared".format(semaphore_key))
    return


  def _semaphore_maybe_auto_signal(self):
    """
    Automatically signal semaphore readiness when conditions are met.

    This method is called automatically from the plugin's process loop.
    It checks if the plugin is ready and signals the semaphore accordingly.

    Readiness is determined by is_plugin_ready() from _PluginReadinessMixin:
      - None: use default (uvicorn_server_started or _init_process_finalized)
      - False: explicitly deferred, wait for set_plugin_ready(True)
      - True: explicitly ready

    Once ready, this method:
      1. Ensures semaphore structure exists
      2. Calls _setup_semaphore_env() hook (if implemented)
      3. Signals readiness via semaphore_set_ready()
      4. Sets _semaphore_signaled flag to prevent duplicate signaling

    Returns
    -------
    None
    """
    # Early exit if semaphore not configured
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return

    # Early exit if already signaled
    if self._semaphore_signaled:
      return

    # Check unified readiness (from _PluginReadinessMixin)
    is_ready = self.is_plugin_ready()

    # Early exit if not ready yet
    if not is_ready:
      return

    # Plugin is ready - signal semaphore
    try:
      # Ensure semaphore structure exists
      self._semaphore_ensure_structure()

      # Call hook method if implemented (for plugin-specific env vars)
      if hasattr(self, '_setup_semaphore_env') and callable(self._setup_semaphore_env):
        self._setup_semaphore_env()

      # Signal readiness
      self.semaphore_set_ready()

      # Mark as signaled to prevent duplicate signaling
      self._semaphore_signaled = True

      self._semaphore_Pd("Semaphore '{}' auto-signaled successfully".format(semaphore_key))
    except Exception as ex:
      self._semaphore_Pd("Error in auto-signal for '{}': {}".format(semaphore_key, ex))
      # Reset flag to allow retry on next iteration
      self._semaphore_signaled = False
    return


  def _setup_semaphore_env(self):
    """
    Hook method for plugins to set semaphore environment variables.

    Override this method in your plugin to expose environment variables
    to paired plugins (e.g., Container App Runners waiting for this plugin).

    This method is called automatically by _semaphore_maybe_auto_signal()
    right before signaling readiness.

    Example Usage
    -------------
    ```python
    def _setup_semaphore_env(self):
      # RedMesh example - expose API connection details
      host = getattr(self, 'cfg_host', None) or '127.0.0.1'
      port = self.cfg_port
      if port:
        self.semaphore_set_env('API_HOST', host)
        self.semaphore_set_env('API_PORT', str(port))
        self.semaphore_set_env('API_URL', 'http://{}:{}'.format(host, port))

      # Keysoft example - expose multiple endpoints
      localhost_ip = self.log.get_localhost_ip()
      port = getattr(self, 'cfg_port', 15033)
      self.semaphore_set_env('PORT', str(port))
      self.semaphore_set_env('RATIO1_AGENT_ENDPOINT',
        'http://{}:{}/query'.format(localhost_ip, port))

      # Container App Runner example - expose container details
      self.semaphore_set_env('CONTAINER_NAME', self._container_name)
      self.semaphore_set_env('CONTAINER_PORT', str(self._exposed_port))
    ```

    Returns
    -------
    None
    """
    # Default implementation is a no-op
    # Plugins override this method to set their specific env vars
    return


  def _semaphore_auto_cleanup(self):
    """
    Automatically clean up semaphore on plugin shutdown.

    This method is called automatically from the plugin's on_close() lifecycle.
    It clears the semaphore to signal to waiting plugins that this dependency
    is no longer available.

    Returns
    -------
    None
    """
    semaphore_key = getattr(self, 'cfg_semaphore', None)
    if not semaphore_key:
      return

    try:
      self.semaphore_clear()
      self._semaphore_Pd("Semaphore '{}' auto-cleanup completed".format(semaphore_key))
    except Exception as ex:
      self._semaphore_Pd("Error in semaphore auto-cleanup for '{}': {}".format(semaphore_key, ex))
    return


  def _semaphore_set_ready_flag(self):
    """
    Set the readiness flag to signal that this plugin is ready.

    This method delegates to set_plugin_ready(True) from _PluginReadinessMixin,
    which provides unified readiness for both semaphore and chainstore mechanisms.

    Use this method instead of directly setting readiness flags.
    This is typically used by plugins with custom readiness conditions
    (e.g., Container App Runner after health checks pass).

    The auto-signal mechanism will detect this flag and signal the semaphore
    automatically on the next process loop iteration.

    Returns
    -------
    None

    Example Usage
    -------------
    ```python
    # Container App Runner - after health check passes
    if probe_result:
      self.P("Health check passed - app is ready!", color='g')
      self._app_ready = True
      self._semaphore_set_ready_flag()  # Signal readiness for auto-signal
    ```
    """
    # Delegate to unified readiness (triggers both semaphore AND chainstore)
    self.set_plugin_ready(True)
    return


  def _semaphore_reset_signal(self):
    """
    Reset semaphore signaling state to allow re-signaling.

    This is useful for plugins that restart their services (e.g., Container App Runner
    restarting containers) and need to signal readiness again after restart.

    Calling this method will:
    - Clear the semaphore (signal to consumers that service is down)
    - Reset the _semaphore_signaled flag (allow re-signaling)
    - Reset unified readiness to False (deferred state)

    Returns
    -------
    None

    Example Usage
    -------------
    ```python
    # Container App Runner - when stopping container before restart
    def _stop_container_and_save_logs_to_disk(self):
      self.P(f"Stopping container app '{self.container_id}' ...")
      self._semaphore_reset_signal()  # Clear and reset for re-signaling
      # ... continue with container stop logic
    ```
    """
    # Clear the semaphore to signal consumers that service is down
    self.semaphore_clear()

    # Reset flags to allow re-signaling after restart
    self._semaphore_signaled = False

    # Reset unified readiness to deferred state (requires explicit set_plugin_ready(True))
    self.set_plugin_ready(False)

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
      return shmem_data.get('is_ready', False)

    # Check all required semaphores
    required_keys = self._semaphore_get_keys()
    if not required_keys:
      return True  # No dependencies, always ready

    for key in required_keys:
      shmem_data = self.plugins_shmem.get(key, {})
      if not shmem_data.get('is_ready', False):
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
      if shmem_data.get('is_ready', False):
        env_vars = shmem_data.get('env', {})
        if env_vars:
          self._semaphore_Pd("Semaphore '{}' provides {} env vars: {}".format(
            key, len(env_vars), list(env_vars.keys())))
        result.update(env_vars)
      else:
        self._semaphore_Pd("Semaphore '{}' not ready, skipping env retrieval".format(key))

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
          'ready': shmem_data.get('is_ready', False),
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
      required_keys = self._semaphore_get_keys()
      self._semaphore_Pd("Starting wait for semaphores: {}".format(required_keys))
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
        shmem_data = self.plugins_shmem.get(key, {})
        metadata = shmem_data.get('metadata', {})
        provider = metadata.get('plugin_signature', 'unknown')
        env_count = len(shmem_data.get('env', {}))
        self._semaphore_Pd("Semaphore '{}' READY (provider: {}, env_vars: {})".format(
          key, provider, env_count))
        self.__semaphore_ready_logged.add(key)
      elif not is_ready:
        all_ready = False

    return all_ready
