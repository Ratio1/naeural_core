"""
chainstore_response_mixin.py

A reusable mixin that provides chainstore response functionality for plugins.

This mixin implements a standard pattern for sending plugin startup confirmations
or other lifecycle events to the chainstore, enabling asynchronous callback mechanisms
for distributed plugin orchestration.

Design Pattern:
--------------
This follows the Mixin Pattern, which allows plugins to compose behaviors by
inheriting from multiple specialized classes. The mixin provides:

1. Template Method Pattern: reset/set methods as templates
2. Strategy Pattern: Subclasses can override _get_chainstore_response_data()
3. Observer Pattern: Chainstore acts as the message broker for observers

Usage (Automatic - via BasePluginExecutor):
-------------------------------------------
This mixin is automatically included in BasePluginExecutor. For simple plugins,
no action is required - chainstore response is sent automatically after on_init().

For complex plugins that need deferred readiness (containers, APIs, etc.),
use set_plugin_ready() from _PluginReadinessMixin:
```python
class MyComplexPlugin(BasePlugin):
  def on_init(self):
    super().on_init()
    self.set_plugin_ready(False)  # Defer until truly ready
    # ... start async initialization ...

  def _after_service_ready(self):
    self.set_plugin_ready(True)  # Triggers both chainstore AND semaphore
```

For custom response data:
```python
class MyPlugin(BasePlugin):
  def _get_chainstore_response_data(self):
    data = super()._get_chainstore_response_data()
    data.update({
      'api_port': self.cfg_port,
      'custom_field': self.custom_value,
    })
    return data
```

Architecture Benefits:
---------------------
1. Single Responsibility: Mixin only handles chainstore response logic
2. Open/Closed: Plugins can extend response data without modifying mixin
3. DRY: Eliminates code duplication across multiple plugin types
4. Testability: Mixin can be tested independently
5. Composability: Can be mixed with other functionality mixins
6. Simplicity: Single write - no retries, no confirmations
7. Process Loop Pattern: Like semaphore, checks readiness in _process()

Configuration:
-------------
CHAINSTORE_RESPONSE_KEY (str, optional):
  The key under which to store the response in chainstore.
  If None or not set, no response will be sent/reset.
  This is typically set by orchestration systems like Deeploy.

Security Considerations:
-----------------------
- Response keys should be generated with sufficient entropy to prevent guessing
- Response data should not contain sensitive information (passwords, tokens, etc.)

"""


class _ChainstoreResponseMixin:
  """
  Mixin providing chainstore response functionality for plugin lifecycle events.

  This mixin enables plugins to send confirmation data to a distributed chainstore
  when important lifecycle events occur (e.g., plugin startup, state changes).

  The mixin uses the Template Method pattern to provide a standard flow while
  allowing subclasses to customize the response data through hook methods.

  Key principle: Reset at start, send when ready (via process loop).
  """

  def __init__(self):
    """
    Initialize chainstore response state variables.

    State variables:
      - _chainstore_response_sent: Prevents duplicate sends

    Note: _is_plugin_ready and set_plugin_ready() are now in _PluginReadinessMixin
    """
    self._chainstore_response_sent = False
    super(_ChainstoreResponseMixin, self).__init__()
    return


  def _chainstore_maybe_auto_send(self):
    """
    Automatically send chainstore response when plugin is ready.

    Called from process loop (_process). Sends only once.
    This follows the same pattern as _semaphore_maybe_auto_signal().

    Uses is_plugin_ready() from _PluginReadinessMixin which resolves:
      - None: use default (uvicorn_server_started or _init_process_finalized)
      - False: explicitly deferred, wait for set_plugin_ready(True)
      - True: explicitly ready
    """
    # No key configured
    if not self._should_send_chainstore_response():
      return

    # Already sent
    if self._chainstore_response_sent:
      return

    # Check unified readiness (from _PluginReadinessMixin)
    if not self.is_plugin_ready():
      return

    # Ready - send response
    if self._send_chainstore_response():
      self._chainstore_response_sent = True
    return

  def _get_chainstore_response_key(self):
    """
    Get the chainstore response key from configuration.

    This method follows the Dependency Inversion Principle by depending on
    configuration abstraction rather than concrete implementation details.

    Returns:
        str or None: The response key if configured, None otherwise.
    """
    return getattr(self, 'cfg_chainstore_response_key', None)

  def _get_chainstore_response_data(self):
    """
    Template method hook: Build the response data dictionary.

    This method can be overridden by subclasses to provide custom response data.
    The default implementation returns base plugin information.

    Design Pattern: Template Method Pattern
    - This is the "hook" method that subclasses can override
    - The parent method _send_chainstore_response() is the "template"

    Best Practice: When overriding, call super() first then extend:
    ```python
    def _get_chainstore_response_data(self):
      data = super()._get_chainstore_response_data()
      data.update({
        'custom_field': self.custom_value,
      })
      return data
    ```

    Returns:
        dict: Response data to be stored in chainstore.
              Should be JSON-serializable.

    Security Note:
        Never include sensitive data like passwords, private keys, or tokens
        in the response data. This data may be visible to multiple nodes.
    """
    # Basic data always available
    data = {
      'plugin_signature': self.get_signature(),
      'instance_id': self.get_instance_id(),
      'timestamp': self.time_to_str(self.time()),
    }

    # Add base plugin fields (from BasePluginExecutor)
    data['stream_id'] = self.get_stream_id()
    data['plugin_version'] = self.__version__
    data['node_id'] = self.ee_id
    data['node_addr'] = self.ee_addr

    # Default status (can be overridden by subclasses)
    if 'status' not in data:
      data['status'] = 'ready'
      data['is_ready'] = True

    # Merge optional custom fields without forcing subclasses to override this method
    extra = {}
    try:
      extra = self.get_chainstore_response()
      if extra is None:
        extra = {}
      elif not isinstance(extra, dict):
        self.P(
          f"get_chainstore_response() must return a dict, got {type(extra)}",
          color='r'
        )
        extra = {}
    except Exception as exc:
      self.P(f"Error in get_chainstore_response(): {exc}", color='r')
      extra = {}

    data.update(extra)
    return data


  def get_chainstore_response(self):
    """
    Public hook to add custom fields to chainstore response data.

    Override this method to append additional JSON-serializable fields
    without replacing the standard response keys provided by
    _get_chainstore_response_data().

    Returns
    -------
    dict
        Extra fields to merge into the response (default: {}).
    """
    return {}


  def _should_send_chainstore_response(self):
    """
    Determine if a chainstore response should be sent.

    This method implements validation logic to ensure responses are only
    sent when properly configured. Can be overridden for custom logic.

    Returns:
        bool: True if response should be sent, False otherwise.
    """
    response_key = self._get_chainstore_response_key()
    if response_key is None:
      return False

    if not isinstance(response_key, str) or len(response_key) == 0:
      self.P(
        "CHAINSTORE_RESPONSE_KEY is configured but invalid (must be non-empty string)",
        color='r'
      )
      return False

    return True

  def _reset_chainstore_response(self):
    """
    Reset (clear) the chainstore response key at plugin start.

    This should be called at the very beginning of plugin initialization to
    signal that the plugin is starting up. The orchestration system can monitor
    this key - if it's None/empty, it means the plugin is still initializing.

    After successful initialization, call _send_chainstore_response() to set
    the actual response data.

    Returns:
        bool: True if reset was performed, False if key not configured.

    Example:
        ```python
        def on_init(self):
            super().on_init()
            self._reset_chainstore_response()  # Clear at start
            # ... initialization code ...
            self._send_chainstore_response()    # Set after success
            return
        ```
    """
    if not self._should_send_chainstore_response():
      return False

    response_key = self._get_chainstore_response_key()
    self.P(f"Resetting chainstore response key '{response_key}'")

    try:
      # Set to None to signal "initializing" state
      result = self.chainstore_set(response_key, None)
      if result:
        self.P(f"Successfully reset chainstore key '{response_key}'")
        return True
      else:
        self.P(f"Failed to reset chainstore key '{response_key}'", color='y')
        return False
    except Exception as e:
      self.P(f"Error resetting chainstore key '{response_key}': {e}", color='r')
      return False

  def _send_chainstore_response(self):
    """
    Send plugin response data to chainstore (single write).

    This is the main template method that sends the response after successful
    plugin initialization. It should be called exactly once at the end of
    on_init() after all setup is complete.

    Design Pattern: Template Method Pattern
    - Defines the skeleton of the algorithm
    - Delegates data building to hook method (_get_chainstore_response_data)

    Args:
        None

    Returns:
        bool: True if response was sent successfully, False otherwise.

    Example:
        ```python
        # Send default response data
        self._send_chainstore_response()
        ```

    Implementation Notes:
        - Single write (no retries, no confirmations)
        - Gracefully handles chainstore_set failures without raising exceptions
        - Call _reset_chainstore_response() at plugin start before calling this
    """
    # Validation: Check if response should be sent
    if not self._should_send_chainstore_response():
      return False

    response_key = self._get_chainstore_response_key()

    self.P(f"Sending chainstore response to key '{response_key}'", color='b')

    # Build response data using template method hook
    try:
      response_data = self._get_chainstore_response_data()

    except Exception as e:
      self.P(
        f"Error building chainstore response data: {e}",
        color='r'
      )
      return False

    # Send single write to chainstore
    try:
      self.P(f"Setting '{response_key}' to: {self.json_dumps(response_data)}")

      # Single write - no retries, no confirmations
      result = self.chainstore_set(response_key, response_data)

      if result:
        self.P(f"Successfully sent chainstore response to '{response_key}'", color='g')
        return True
      else:
        self.P(f"Failed to send chainstore response (chainstore_set returned False)", color='y')
        return False

    except Exception as e:
      self.P(f"Error sending chainstore response: {e}", color='r')
      return False
