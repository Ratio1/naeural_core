"""
Plugin Readiness Mixin

This mixin provides unified plugin readiness management that is used by
both chainstore response and semaphore signaling mechanisms.

The readiness state is a single source of truth that controls when:
- Chainstore responses are sent to orchestration systems
- Semaphore signals are sent to paired plugins

Usage:
  Simple plugins: No action needed - auto-ready after _init_process_finalized
  FastAPI plugins: No action needed - auto-ready when uvicorn_server_started = True
  Complex plugins: Call set_plugin_ready(False) in on_init(), then set_plugin_ready(True) when ready
"""


class _PluginReadinessMixin:
  """
  Unified plugin readiness management.

  Provides a single source of truth for plugin readiness that is used by
  both chainstore response and semaphore signaling mechanisms.

  Tri-state semantics:
    - None: Use default readiness (uvicorn_server_started, _init_process_finalized)
    - False: Explicitly deferred (complex plugins waiting for async resources)
    - True: Explicitly ready

  Example Usage
  -------------
  Simple plugin (no action needed):
  ```python
  class SimplePlugin(BasePlugin):
      pass  # Auto-ready after _init_process_finalized
  ```

  Complex plugin (deferred readiness):
  ```python
  class ContainerPlugin(BasePlugin):
      def on_init(self):
          super().on_init()
          self.set_plugin_ready(False)  # Defer until container ready

      def _on_container_healthy(self):
          self.set_plugin_ready(True)   # Triggers both chainstore + semaphore
  ```
  """

  def __init__(self):
    self._is_plugin_ready = None  # tri-state: None, False, True
    super(_PluginReadinessMixin, self).__init__()
    return

  def set_plugin_ready(self, ready=True):
    """
    Set plugin readiness state.

    This single API controls readiness for all mechanisms:
    - Chainstore response (sends to chainstore)
    - Semaphore signaling (signals paired plugins)

    Parameters
    ----------
    ready : bool, default True
        True: Plugin is ready (triggers chainstore + semaphore signals)
        False: Defer readiness (wait for async resources)

    Returns
    -------
    None

    Examples
    --------
    Defer readiness in on_init():
    >>> self.set_plugin_ready(False)

    Signal ready when async resource is available:
    >>> self.set_plugin_ready(True)

    Or simply (uses default=True):
    >>> self.set_plugin_ready()
    """
    self._is_plugin_ready = ready
    return


  def is_plugin_ready(self):
    """
    Check if plugin is ready.

    Resolves tri-state to boolean:
    - False: Explicitly deferred -> return False
    - True: Explicitly ready -> return True
    - None: Use default checks -> return _get_default_readiness()

    Returns
    -------
    bool
        True if plugin is ready, False otherwise
    """
    if isinstance(self._is_plugin_ready, bool):
      return self._is_plugin_ready
    return self._get_default_readiness()


  def _get_default_readiness(self):
    """
    Default readiness check for plugins that don't explicitly set readiness.

    Checks in order:
    1. uvicorn_server_started (FastAPI plugins)
    2. _init_process_finalized (all plugins)

    This method can be overridden by subclasses to add custom default
    readiness conditions.

    Returns
    -------
    bool
        True if default conditions indicate readiness
    """
    # FastAPI plugins: wait for uvicorn server
    if hasattr(self, 'uvicorn_server_started'):
      return self.uvicorn_server_started
    # All plugins: wait for init finalization
    return getattr(self, '_init_process_finalized', False)
