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

import random

from naeural_core import constants as ct


class _DeeployChainstoreResponseMixin:
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
    super(_DeeployChainstoreResponseMixin, self).__init__()
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


  def reset_chainstore_response(self):
    """
    Reset chainstore response state for plugin restart/recycle.

    Call this when preparing for a restart to:
    1. Clear the chainstore key (signals "restarting" to orchestrators)
    2. Allow a new response to be sent when ready again

    Typically paired with set_plugin_ready(False).

    Returns
    -------
    bool
        True if reset was performed, False if key not configured.

    Example
    -------
    ```python
    def _restart_container(self):
        self.reset_chainstore_response()
        self.set_plugin_ready(False)
        # ... perform restart ...
    ```
    """
    return self._reset_chainstore_response()


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
    # Reset the sent flag to allow re-sending after reset
    self._chainstore_response_sent = False
    return self._reset_chainstore_response_key(response_key)

  def _reset_chainstore_response_key(self, response_key, write_kwargs=None):
    """
    Reset an explicit chainstore response key.

    This is the reusable primitive for both the plugin's configured
    `CHAINSTORE_RESPONSE_KEY` and Deeploy's pre-dispatch batch reset of
    arbitrary response keys.
    """
    if not isinstance(response_key, str) or len(response_key) == 0:
      self.P(
        f"Invalid chainstore response key for reset: {response_key}",
        color='r',
      )
      return False

    self.P(f"Resetting chainstore response key '{response_key}'")

    if write_kwargs is None:
      write_kwargs = self._get_chainstore_response_write_kwargs()

    try:
      # Set to None to signal "initializing" state
      result = self.chainstore_set(
        response_key,
        None,
        **write_kwargs
      )
      if result:
        self.P(f"Successfully reset chainstore key '{response_key}'")
        return True
      else:
        self.P(f"Failed to reset chainstore key '{response_key}'", color='y')
        return False
    except Exception as e:
      self.P(f"Error resetting chainstore key '{response_key}': {e}", color='r')
      return False

  def _get_chainstore_response_seed_nodes(self):
    """
    Return the configured seed oracle addresses for the current EVM network.

    Returns
    -------
    list
        Seed oracle addresses from network constants, or an empty list when the
        constant is missing or invalid.
    """
    try:
      seed_nodes = ct.CURRENT_EVM_NET_CONSTANTS.get(
        ct.BASE_CT.EvmNetConstants.SEED_NODES_ADDRESSES_KEY,
        []
      )
    except Exception as exc:
      self.P(f"Unable to read seed oracle addresses for chainstore response: {exc}", color='y')
      return []

    if not isinstance(seed_nodes, (list, tuple, set)):
      self.P(
        f"Seed oracle addresses for chainstore response must be a list, got {type(seed_nodes)}",
        color='y'
      )
      return []

    return list(seed_nodes)

  def _select_chainstore_response_seed_peer(self, seed_peers):
    """
    Select one seed oracle for chainstore response replication.

    This method is intentionally isolated so tests can override deterministic
    selection without patching global randomness.
    """
    if len(seed_peers) == 0:
      return None
    return random.choice(seed_peers)

  def _get_chainstore_response_peers(self):
    """
    Build the restricted peer list for Deeploy chainstore responses.

    The response should be visible only on the oracle that initiated the
    create/update operation and one random seed oracle. Default ChainStore
    peers and configured plugin peers are intentionally not used for this
    response channel.
    """
    peers = []

    modified_by_addr = getattr(self, 'modified_by_addr', None)
    if modified_by_addr:
      peers.append(modified_by_addr)
    else:
      self.P("Missing modified_by_addr for chainstore response peer routing", color='y')

    seed_nodes = [
      peer
      for peer in self._get_chainstore_response_seed_nodes()
      if isinstance(peer, str) and len(peer) > 0
    ]
    if len(seed_nodes) == 0:
      self.P(
        "No seed oracle addresses configured for chainstore response peer routing",
        color='y'
      )
      return peers

    candidate_seed_nodes = [peer for peer in seed_nodes if peer not in peers]
    if len(candidate_seed_nodes) == 0:
      candidate_seed_nodes = seed_nodes

    seed_peer = self._select_chainstore_response_seed_peer(candidate_seed_nodes)
    if seed_peer and seed_peer not in peers:
      peers.append(seed_peer)
    return peers

  def _get_chainstore_response_local_reset_peers(self):
    """
    Build explicit peers for response-key reset writes sent by the initiator.

    The local ChainStore write clears the initiating oracle. The explicit peer
    list adds one seed oracle and intentionally excludes app chainstore peers,
    configured chainstore peers, and backend default peers.
    """
    seed_nodes = [
      peer
      for peer in self._get_chainstore_response_seed_nodes()
      if isinstance(peer, str) and len(peer) > 0
    ]
    if len(seed_nodes) == 0:
      self.P(
        "No seed oracle addresses configured for chainstore response reset routing",
        color='y'
      )
      return []

    current_addr = getattr(self, 'ee_addr', None)
    candidate_seed_nodes = [peer for peer in seed_nodes if peer != current_addr]
    if len(candidate_seed_nodes) == 0:
      candidate_seed_nodes = seed_nodes

    seed_peer = self._select_chainstore_response_seed_peer(candidate_seed_nodes)
    return [seed_peer] if seed_peer else []

  def _get_chainstore_response_local_reset_write_kwargs(self):
    """
    Return restricted ChainStore write arguments for local response-key resets.
    """
    return {
      'extra_peers': self._get_chainstore_response_local_reset_peers(),
      'include_default_peers': False,
      'include_configured_peers': False,
      'debug': True,
    }

  def _get_chainstore_response_write_kwargs(self):
    """
    Return common restricted ChainStore write arguments for response writes.
    """
    return {
      'extra_peers': self._get_chainstore_response_peers(),
      'include_default_peers': False,
      'include_configured_peers': False,
      'debug': True,
    }

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
      result = self.chainstore_set(
        response_key,
        response_data,
        **self._get_chainstore_response_write_kwargs()
      )

      if result:
        self.P(f"Successfully sent chainstore response to '{response_key}'", color='g')
        return True
      else:
        self.P(f"Failed to send chainstore response (chainstore_set returned False)", color='y')
        return False

    except Exception as e:
      self.P(f"Error sending chainstore response: {e}", color='r')
      return False
