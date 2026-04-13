
class _BasePluginAPIMixin:
  def __init__(self) -> None:
    super(_BasePluginAPIMixin, self).__init__()
    
    self.__chain_state_initialized = False
    return
  
  # Obsolete
  def _pre_process(self):
    """
    Called before process. Currently (partially) obsolete

    Returns
    -------
    TBD.

    """
    return
  
  def _post_process(self):
    """
    Called after process. Currently (partially) obsolete

    Returns
    -------
    TBD.

    """
    return
  
  
  def step(self):
    """
    The main code of the plugin (loop iteration code). Called at each iteration of the plugin loop.

    Returns
    -------
    None.

    """
    return
  
  
  def process(self):
    """
    The main code of the plugin (loop iteration code). Called at each iteration of the plugin loop.

    Returns
    -------
    Payload.

    """
    return self.step()
  
  def _process(self):
    """
    The main code of the plugin (loop iteration code.

    Returns
    -------
    Payload.

    """
    return self.process()

  
  def on_init(self):
    """
    Called at init time in the plugin thread.

    Returns
    -------
    None.

    """      
    return
  
  def _on_init(self):
    """
    Called at init time in the plugin thread.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_init` called for plugin initialization...")
    self.on_init()
    return


  def on_close(self):
    """
    Called at shutdown time in the plugin thread.

    Returns
    -------
    None.

    """      
    return


  def _on_close(self):
    """
    Called at shutdown time in the plugin thread.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_close` called for plugin cleanup at shutdown...")
    self.maybe_archive_upload_last_files()
    # Auto-cleanup semaphore if configured
    self._semaphore_auto_cleanup()
    self.on_close()
    return

  def on_command(self, data, **kwargs):
    """
    Called when a INSTANCE_COMMAND is received by the plugin instance.
    
    The command is sent via `cmdapi_send_instance_command` as in below simplified example:
    
    ```python
      pipeline = "some_app_pipeline"
      signature = "CONTAINER_APP_RUNNER"
      instance_id = "CONTAINER_APP_1e8dac"
      node_address = "0xai_1asdfG11sammamssdjjaggxffaffaheASSsa"
      
      instance_command = "RESTART"
      
      plugin.cmdapi_send_instance_command(
        pipeline=pipeline,
        signature=signature,
        instance_id=instance_id,
        instance_command=instance_command,
        node_address=node_address,
      )
    ```
    
    while the `on_command` method should look like this:
    
    ```python
      def on_command(self, data, **kwargs):
        if data == "RESTART":
          self.P("Restarting ...")
        elif data == "STOP":
          self.P("Stopping ...")
        else:
          self.P(f"Unknown command: {data}")
        return
    ```
    
    The `instance_command` is passed to this method as `data` and in fact can be a dict with extra data. If
    `instance_command` contains `COMMAND_PARAMS` dict then all the key-value pairs in the `COMMAND_PARAM` dict 
    will be passed as kwargs to this method - see below example:
    
    ```python
      instance_command = {
        "COMMAND_PARAMS": {
          "some_param1": "value1",
          "some_param2": "value2",
        }
      }
      
      plugin.cmdapi_send_instance_command(
        pipeline=pipeline,
        signature=signature,
        instance_id=instance_id,
        instance_command=instance_command,
        node_address=node_address,
      )
    ```
    Then this `on_command` method should look like this:
    
    ```python
    
      def on_command(self, data, some_param1=None, some_param2=None, **kwargs):
        if some_param1:
          self.P(f"Received some_param1: {some_param1}")
        if some_param2:
          self.P(f"Received some_param2: {some_param2}")
        # Process the command here
        return
    ```

    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    """
    return

  def _on_command(self, data, default_configuration=None, current_configuration=None, **kwargs):
    """
    Called when a INSTANCE_COMMAND is received by the plugin instance.
    
    The command is sent via `cmdapi_send_instance_command` as in below simplified example:
    
    ```python
      pipeline = "some_app_pipeline"
      signature = "CONTAINER_APP_RUNNER"
      instance_id = "CONTAINER_APP_1e8dac"
      node_address = "0xai_1asdfG11sammamssdjjaggxffaffaheASSsa"
      
      instance_command = "RESTART"
      
      plugin.cmdapi_send_instance_command(
        pipeline=pipeline,
        signature=signature,
        instance_id=instance_id,
        instance_command=instance_command,
        node_address=node_address,
      )
    ```
    
    while the `on_command` method should look like this:
    
    ```python
      def on_command(self, data, **kwargs):
        if data == "RESTART":
          self.P("Restarting ...")
        elif data == "STOP":
          self.P("Stopping ...")
        else:
          self.P(f"Unknown command: {data}")
        return
    ```
    
    The `instance_command` is passed to this method as `data` and in fact can be a dict with extra data. If
    `instance_command` contains `COMMAND_PARAMS` dict then all the key-value pairs in the `COMMAND_PARAM` dict 
    will be passed as kwargs to this method - see below example:
    
    ```python
      instance_command = {
        "COMMAND_PARAMS": {
          "some_param1": "value1",
          "some_param2": "value2",
        }
      }
      
      plugin.cmdapi_send_instance_command(
        pipeline=pipeline,
        signature=signature,
        instance_id=instance_id,
        instance_command=instance_command,
        node_address=node_address,
      )
    ```
    Then this `on_command` method should look like this:
    
    ```python
    
      def on_command(self, data, some_param1=None, some_param2=None, **kwargs):
        if some_param1:
          self.P(f"Received some_param1: {some_param1}")
        if some_param2:
          self.P(f"Received some_param2: {some_param2}")
        # Process the command here
        return
    ```
    
    Parameters
    ----------
    data : any
      object, string, etc.

    Returns
    -------
    None.

    """
    self.P("Default plugin `_on_command`...")

    if (isinstance(data, str) and data.upper() == 'DEFAULT_CONFIGURATION') or default_configuration:
      self.P("Received \"DEFAULT_CONFIGURATION\" command...")
      self.add_payload_by_fields(
        default_configuration=self._default_config,
        command_params=data,
      )
      return
    if (isinstance(data, str) and data.upper() == 'CURRENT_CONFIGURATION') or current_configuration:
      self.P("Received \"CURRENT_CONFIGURATION\" command...")
      self.add_payload_by_fields(
        current_configuration=self._upstream_config,
        command_params=data,
      )
      return

    self.on_command(data, **kwargs)
    return


  def _on_config(self):
    """
    Called when the instance has just been reconfigured

    Parameters
    ----------
    None

    Returns
    -------
    None.

    """
    self.P("Default plugin {} `_on_config` called...".format(self.__class__.__name__))
    if hasattr(self, 'on_config'):
      self.on_config()
    return


  ###
  ### Chain State
  ### 
  
  def __chainstorage_memory(self):
    return self.plugins_shmem
  
  def __maybe_wait_for_chain_state_init(self):
    # TODO: raise exception if not found after a while

    while not self.plugins_shmem.get('__chain_storage_set'):
      self.sleep(0.1)
    
    if not self.__chain_state_initialized:
      self.P(" ==== Chain state initialized.")
    self.__chain_state_initialized = True
    return
  
  def __normalize_chainstore_peers(self, peers):
    """
    Normalize chain-store peer selectors.

    Parameters
    ----------
    peers : str or list or any
      Peer selector provided by plugin configuration or per-call overrides.
      Strings are promoted to one-element lists. Non-list, non-string values
      are treated as empty input.

    Returns
    -------
    list of str
      De-duplicated peer addresses with empty values and the local node address
      removed.
    """
    if isinstance(peers, str):
      peers = [peers]
    elif not isinstance(peers, list):
      peers = []
    result = []
    for peer in peers:
      if not isinstance(peer, str) or len(peer) == 0:
        continue
      if peer == self.ee_addr or peer in result:
        continue
      result.append(peer)
    return result


  def __chainstore_specific_peers(self, extra_peers=None, include_configured_peers=True):
    """
    Build the explicit peer override list for one chain-store call.

    This helper keeps ``chainstore_set`` and ``chainstore_hsync`` aligned so
    both operations interpret configured peers, per-call peers, de-duplication,
    and self-exclusion in exactly the same way.

    Parameters
    ----------
    extra_peers : str or list, optional
      Additional peer addresses requested by the caller for this operation.
    include_configured_peers : bool, optional
      If True, include ``cfg_chainstore_peers`` before appending
      ``extra_peers``. Default is True.

    Returns
    -------
    list or None
      Explicit peer addresses to forward to the backend helper. ``None`` means
      that the caller did not request any explicit peer override and the
      backend should rely only on its default peer set.
    """
    specific_peers = []
    if include_configured_peers:
      specific_peers.extend(
        self.__normalize_chainstore_peers(self.cfg_chainstore_peers or [])
      )
    for peer in self.__normalize_chainstore_peers(extra_peers):
      if peer not in specific_peers:
        specific_peers.append(peer)
    return specific_peers or None


  def chainstore_set(
    self,
    key,
    value,
    readonly=False,
    token=None,
    debug=False,
    extra_peers=None,
    include_default_peers=True,
    include_configured_peers=True,
    timeout=None,
    max_retries=None,
  ):
    """
    Set data in the R1 Chain Storage.

    This method stores a key-value pair in the distributed chain storage, broadcasting
    the data to peer nodes for replication and waiting for confirmations.

    IMPORTANT - JSON Normalization:
    -------------------------------
    Values are automatically normalized through JSON serialization before storage.
    This ensures deterministic behavior across the distributed network, as all nodes
    will store and compare identical data structures regardless of the original
    Python types used.

    Key implications of JSON normalization:
      - Dictionary keys are converted to strings (e.g., {8080: "http"} becomes {"8080": "http"})
      - Values must be JSON-serializable (no datetime, bytes, custom objects without serialization)
      - Numeric precision follows JSON spec (integers preserved, floats may have precision limits)
      - Dictionary ordering is preserved (Python 3.7+)

    Example:
    --------
    ```python
    # Integer keys are converted to string keys
    data = {'ports': {8080: 'http', 443: 'https'}}
    self.chainstore_set('my_key', data)

    # When retrieved, keys will be strings:
    result = self.chainstore_get('my_key')
    # result = {'ports': {'8080': 'http', '443': 'https'}}
    ```

    Parameters
    ----------
    key : str
      The key under which to store the value. Should be a unique identifier
      within the chain storage namespace.

    value : any (JSON-serializable)
      The value to store. Must be JSON-serializable. Complex types like
      datetime objects should be converted to strings before storing.

    readonly : bool, optional
      If True, the key-value pair becomes read-only and cannot be overwritten
      by other owners. Default is False.

    token : any, optional
      A token for access control. If provided, subsequent read/write operations
      on this key must provide the same token. Default is None.

    debug : bool, optional
      If True, enables verbose logging of the operation including peer counts,
      confirmation status, and timing information. Default is False.

    extra_peers : list, optional
      Additional peer addresses to target for this call. Default is None.

    include_default_peers : bool, optional
      If True, include the backend chain-store default peer set in addition to
      any explicit peers. If False, target only the explicit peer set built
      from configured and extra peers. Default is True.

    include_configured_peers : bool, optional
      If True, include ``cfg_chainstore_peers`` in the explicit peer set for
      this call. If False, only ``extra_peers`` are used as explicit targets.
      Default is True.

    timeout : float, optional
      Per-attempt confirmation wait timeout in seconds. If None, use the
      backend default behavior. Default is None.

    max_retries : int, optional
      Maximum number of timeout-triggered rebroadcast retries for this write.
      If None, use the backend default behavior. Default is None.

    Returns
    -------
    bool
      True if the value was successfully stored and confirmed by peers,
      False if the operation failed (timeout, no confirmations, etc.)

    See Also
    --------
    chainstore_get : Retrieve a value from chain storage
    chainstore_hset : Store a value in a hash set within chain storage
    """
    self.start_timer("chainstore_set")
    memory = self.__chainstorage_memory()
    result = False
    try:
      self.__maybe_wait_for_chain_state_init()
      func = memory.get('__chain_storage_set')

      # JSON normalization: ensure deterministic storage by normalizing through JSON.
      # This converts integer dict keys to strings and ensures consistent data structures
      # across all nodes in the distributed network. Without this normalization, the
      # local storage could have {8080: "http"} while the transmitted/received data
      # would have {"8080": "http"}, causing confirmation comparisons to fail.
      value = self.json_loads(self.json_dumps(value))

      specific_peers = self.__chainstore_specific_peers(
        extra_peers=extra_peers,
        include_configured_peers=include_configured_peers,
      )
      
      if func is not None:
        if debug:
          self.P("Setting data: {} -> {}".format(key, value), color="green")
        result = func(
          key, value, 
          readonly=readonly,
          token=token,
          peers=specific_peers,
          include_default_peers=include_default_peers,
          timeout=timeout,
          max_retries=max_retries,
          debug=debug,
        )
        elapsed = self.end_timer("chainstore_set")        
        if debug:
          self.P(" ====> `chainstore_set`in {:.4f}s".format(elapsed), color="green")
      else:
        elapsed = self.end_timer("chainstore_set")
        if debug:
          self.P("No chain storage set function found in {:.4f}s".format(elapsed), color="red")
    except Exception as ex:
      elapsed = self.end_timer("chainstore_set")
      msg = "Error in chainstore_set: {} after {:.4f}s".format(ex, elapsed)
      self.P(msg, color="red")      
    return result
  
  
  def chainstore_get(self, key, token=None, debug=False):
    """
    Get data from the R1 Chain Storage
    
    Parameters
    ----------
    key : str
      Key
      
    token : any, optional
      Token, by default None
    """
    self.start_timer("chainstore_get")
    memory = self.__chainstorage_memory()
    self.__maybe_wait_for_chain_state_init()
    value = None
    msg = ""
    try:
      start_search = self.time()
      found = True
      while memory.get('__chain_storage_get') is None:
        self.sleep(0.1)
        if self.time() - start_search > 10:
          msg = "Error: chain storage get function not found after 10 seconds"
          self.P(msg, color="red")
          found = False
          break
      func = memory.get('__chain_storage_get')
      if func is not None:
        value = func(key, token=token, debug=debug)
        elapsed = self.end_timer("chainstore_get")
        if debug:
          self.P("====> `chainstore_get`: {} -> {} in {:.4f}s".format(key, value, elapsed))
      else:
        elapsed = self.end_timer("chainstore_get")
        if debug:
          self.P("No chain storage get function found in {:.4f}s".format(elapsed), color="red")
    except Exception as ex:
      elapsed = self.end_timer("chainstore_get")  
      msg = "Error in chainstore_get: {} after {:.4f}s".format(ex, elapsed)
      self.P(msg, color="red")
    return value

  
  def __hset_index(self, hkey):
    hkey_hash = self.get_hash(hkey, algorithm='sha256', length=10)
    return f"hs:{hkey_hash}:"

  
  def __hset_key(self, hkey, key):
    b64key = self.str_to_base64(key, url_safe=True)
    return self.__hset_index(hkey) + b64key

  
  def chainstore_hget(self, hkey, key, token=None, debug=False):
    """    
    This is a basic implementation of a hash get operation in the chain storage.
    It uses a hash-based string composition to create a composed key.
    """
    start_1 = self.time()
    composed_key = self.__hset_key(hkey, key)
    elapsed_1 = self.time() - start_1
    start_2 = self.time()
    result = self.chainstore_get(composed_key, token=token, debug=debug)
    elapsed_2 = self.time() - start_2
    if debug:
      self.P(f"HGET: '{composed_key}' (index_time={elapsed_1:.4f}s, get_time={elapsed_2:.4f}s)")
    return result  

  def chainstore_hset(
    self,
    hkey,
    key,
    value,
    readonly=False,
    token=None,
    debug=False,
    extra_peers=None,
    include_default_peers=True,
    include_configured_peers=True,
    timeout=None,
    max_retries=None,
  ):
    """
    Store one field of a hash-like namespace in chain storage.

    This is a basic implementation of a hash set operation in the chain
    storage. It uses a hash-based string composition to create a composed key
    and then delegates the actual write, routing, and confirmation behavior to
    ``chainstore_set``.

    Parameters
    ----------
    hkey : str
      Logical hash namespace.
    key : str
      Field name stored under ``hkey``.
    value : any
      Value stored for the field. Must be JSON-serializable under the same
      constraints as ``chainstore_set``.
    readonly : bool, optional
      If True, the composed key becomes read-only for other owners.
      Default is False.
    token : any, optional
      Access token associated with the composed key. Default is None.
    debug : bool, optional
      If True, enable verbose timing and routing logs. Default is False.
    extra_peers : list, optional
      Additional peer addresses to target for this call. Default is None.
    include_default_peers : bool, optional
      If True, include the backend chain-store default peer set. Default is
      True.
    include_configured_peers : bool, optional
      If True, include ``cfg_chainstore_peers`` in the explicit target set.
      Default is True.
    timeout : float, optional
      Per-attempt confirmation wait timeout in seconds. If None, use the
      backend default behavior. Default is None.
    max_retries : int, optional
      Maximum number of timeout-triggered rebroadcast retries. If None, use
      the backend default behavior. Default is None.

    Returns
    -------
    bool
      True if the composed key was stored successfully, False otherwise.

    See Also
    --------
    chainstore_set : Underlying chain-store write operation.
    chainstore_hget : Retrieve one field from a hash-like namespace.
    """
    start_1 = self.time()
    composed_key = self.__hset_key(hkey, key)
    elapsed_1 = self.time() - start_1
    start_2 = self.time()
    result = self.chainstore_set(
      composed_key,
      value,
      readonly=readonly,
      token=token,
      debug=debug,
      extra_peers=extra_peers,
      include_default_peers=include_default_peers,
      include_configured_peers=include_configured_peers,
      timeout=timeout,
      max_retries=max_retries,
    )
    elapsed_2 = self.time() - start_2
    if debug:
      self.P(f"HSET: '{composed_key}' (index_time={elapsed_1:.4f}s, set_time={elapsed_2:.4f}s)")
    return result

  
  def chainstore_hlist(self, hkey : str, token=None, debug=False):
    index = self.__hset_index(hkey)
    memory = self.__chainstorage_memory()
    self.__maybe_wait_for_chain_state_init()
    chain_storage = memory.get('__chain_storage')
    result = []
    for key in chain_storage:
      if not isinstance(key, str) or key == "":
        self.P("Invalid key type in chain storage: {}".format(type(key)), color="red")
        continue
      if key.startswith(index):
        b64field = key[len(index):]
        field = self.base64_to_str(b64field, url_safe=True)
        result.append(field)
      #end if 
    #end for
    return result

  
  def chainstore_hkeys(self, hkey : str, token=None, debug=False):
    return self.chainstore_hlist(hkey, token=token, debug=debug)


  def chainstore_hgetall(self, hkey : str, token=None, debug=False):
    keys = self.chainstore_hlist(hkey, token=token, debug=debug)
    result = {}
    for key in keys:
      value = self.chainstore_hget(hkey, key, token=token, debug=debug)
      result[key] = value
    return result


  def chainstore_hsync(
    self,
    hkey,
    debug=False,
    extra_peers=None,
    include_default_peers=True,
    include_configured_peers=True,
    timeout=None,
  ):
    """
    Refresh one hash namespace from a live peer snapshot.

    The runtime implementation exports one peer snapshot for ``hkey`` and then
    merges it into the local replica. The merge is additive: remote fields
    overwrite stale overlaps, while local-only fields remain untouched.

    Parameters
    ----------
    hkey : str
      Logical hash namespace to refresh.
    debug : bool, optional
      If True, enable verbose timing and routing logs. Default is False.
    extra_peers : str or list, optional
      Additional peer addresses to target for this request. Default is None.
    include_default_peers : bool, optional
      If True, include the backend chain-store default peer set in addition to
      any explicit peers. If False, target only the explicit peer set built
      from configured and extra peers. Default is True.
    include_configured_peers : bool, optional
      If True, include ``cfg_chainstore_peers`` in the explicit peer set for
      this call. If False, only ``extra_peers`` are used as explicit targets.
      Default is True.
    timeout : float, optional
      Maximum time in seconds to wait for one valid peer snapshot. If None,
      use the backend default behavior. Default is None.

    Returns
    -------
    dict
      A result envelope with the refreshed ``hkey``, the accepted peer, and
      the number of merged fields.

    Raises
    ------
    ValueError
      If ``hkey`` is invalid, the backend helper is unavailable, or the peer
      refresh fails.
    """
    self.start_timer("chainstore_hsync")
    memory = self.__chainstorage_memory()
    try:
      self.__maybe_wait_for_chain_state_init()
      func = memory.get("__chain_storage_hsync")
      if func is None:
        raise ValueError("Chain storage hsync function not found.")

      specific_peers = self.__chainstore_specific_peers(
        extra_peers=extra_peers,
        include_configured_peers=include_configured_peers,
      )

      result = func(
        hkey,
        peers=specific_peers,
        include_default_peers=include_default_peers,
        timeout=timeout,
        debug=debug,
      )
      elapsed = self.end_timer("chainstore_hsync")
      if debug:
        self.P(
          "====> `chainstore_hsync`: {} -> {} in {:.4f}s".format(
            hkey, result, elapsed
          )
        )
      return result
    except Exception as ex:
      elapsed = self.end_timer("chainstore_hsync")
      msg = "Error in chainstore_hsync: {} after {:.4f}s".format(ex, elapsed)
      self.P(msg, color="red")
      raise ValueError(msg) from ex
    
  
  # # @property
  # # This CANNOT be a property, as it can be a blocking operation.
  # def _chainstorage(self): # TODO: hide/move/protect this
  #   self.__maybe_wait_for_chain_state_init()
  #   return self.plugins_shmem.get('__chain_storage')

  
  def get_instance_path(self):
    return [self.ee_id, self._stream_id, self._signature, self.cfg_instance_id]  
  
  ###
  ### END Chain State
  ###
  
    
