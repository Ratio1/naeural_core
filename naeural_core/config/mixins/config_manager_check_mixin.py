from naeural_core import constants as ct
import json

class _ConfigManagerCheckMixin(object):
  def __init__(self):
    super(_ConfigManagerCheckMixin, self).__init__()
    return

  def __append_exception_message(self, messages, str_message, extra=None):
    if extra is not None:
      str_message += '\n{}'.format(json.dumps(extra))

    messages.append({'notif' : ct.STATUS_TYPE.STATUS_EXCEPTION, 'msg' : str_message})
    self.P(str_message, color='r')
    return

  def _check_config_app(self, config_app):
    """
    Receives an application config, checks it and maybe cleans it in order to start the application.

    Parameters:
    ----------
    config_app: dict, mandatory
      application config

    Returns:
    -------
    config_app: dict
      the maybe cleaned application config
    """
    messages = []
    can_start_app = True

    # check first if 'COMMUNICATION' is configured
    if 'COMMUNICATION' not in config_app:
      crt_msg = "ERROR! 'COMMUNICATION' not configured in config app."
      self.__append_exception_message(messages, crt_msg)
      can_start_app = False
    else:
      _params = config_app['COMMUNICATION'].get('PARAMS', {})
      _type = config_app['COMMUNICATION'].get('TYPE', '')
      _instances = config_app['COMMUNICATION'].get('INSTANCES', {})

      if not bool(_params):
        crt_msg = "ERROR! 'PARAMS' dictionary not configured in config_app's 'COMMUNICATION' zone"
        self.__append_exception_message(messages, crt_msg)
        can_start_app = False

      if not bool(_type):
        crt_msg = "ERROR! 'TYPE' not configured in config_app's 'COMMUNICATION' zone"
        self.__append_exception_message(messages, crt_msg)
        can_start_app = False

      if not bool(_instances):
        crt_msg = "ERROR! 'INSTANCES' not configured in config_app's 'COMMUNICATION' zone"
        self.__append_exception_message(messages, crt_msg)
        can_start_app = False
    #endif


    for msg in messages:
      self._create_notification(**msg)

    if not can_start_app:
      raise RuntimeError("Application cannot start. Please see above errors.")

    return config_app

  def _check_config_stream(self, config_stream):
    """
    Receives a stream configuration, does some checks and determines whether the stream configuration is good or not

    Parameters:
    ----------
    config_stream: dict, mandatory
      the stream configuration

    Returns:
    --------
    is_good_stream: bool
      Whether the stream configuration is good or not
    """
    messages = []
    is_good_stream = True
    stream_name = config_stream['NAME']

    stream_type = config_stream.get('TYPE', None)
    if stream_type is None:
      crt_msg = "WARNING! 'TYPE' not configured for stream `{}`. Dropping stream `{}`".format(stream_name, stream_name)
      self.__append_exception_message(messages, crt_msg)
      is_good_stream = False
    #endif

    for msg in messages:
      self._create_notification(
        initiator_id=config_stream.get(ct.PAYLOAD_DATA.INITIATOR_ID),
        session_id=config_stream.get(ct.PAYLOAD_DATA.SESSION_ID),
        stream_name=stream_name,
        **msg
      )

    return is_good_stream

  def _check_config_plugin(self, stream_name, config_plugin):
    """
    Receives a plugin configuration, does some checks and determines whether the plugin configuration is good or not

    Parameters:
    ----------
    stream_name: str, mandatory
      the name of the stream where the plugin is configured

    config_plugin: dict, mandatory
      the plugin configuration

    Returns:
    --------
    is_good_plugin: bool
      Whether the plugin configuration is good or not
    """
    messages = []
    is_good_plugin = True

    if 'SIGNATURE' not in config_plugin:
      crt_msg = "WARNING! 'SIGNATURE' not configured for plugin in stream `{}`. Dropping plugin".format(stream_name)
      self.__append_exception_message(messages, crt_msg, config_plugin)
      is_good_plugin = False

    lst_config_instances = config_plugin.get('INSTANCES', [])
    if len(lst_config_instances) == 0:
      crt_msg = "WARNING! 'INSTANCES' not configured for plugin in stream `{}`. Dropping plugin".format(stream_name)
      self.__append_exception_message(messages, crt_msg, config_plugin)
      is_good_plugin = False

    for msg in messages:
      self._create_notification(
        stream_name=stream_name,
        **msg
      )

    return is_good_plugin

  def _check_config_instance(self, stream_name, plugin_signature, config_instance):
    """
    Receives a plugin instance configuration, does some checks and determines whether the plugin instance configuration is good or not

    Parameters:
    ----------
    stream_name: str, mandatory
      the name of the stream where the plugin instance is configured

    plugin_signature: str, mandatory
      the signature of the plugin

    config_instance: dict, mandatory
      the plugin instance configuration

    Returns:
    --------
    is_good_instance: bool
      Whether the plugin instance configuration is good or not
    """
    messages = []
    is_good_instance = True

    if False:
      self.P("Checking config instance for Stream:'{}' -> Plugin:'{}' -> Instance config:{}".format(
        stream_name, plugin_signature, 
        '\n{}'.format(self.log.dict_pretty_format(config_instance)) if isinstance(config_instance, dict) else "'{}'".format(config_instance),
        ),
        color='m'
      )


    
    if 'INSTANCE_ID' not in config_instance:
      config_instance['INSTANCE_ID'] = self.log.now_str()      
    
    if 'INSTANCE_ID' not in config_instance:
      crt_msg = "WARNING! 'INSTANCE_ID' not configured in plugin `{}`, stream `{}`. Dropping instance".format(plugin_signature, stream_name)
      self.__append_exception_message(messages, crt_msg, config_instance)
      is_good_instance = False

    points = config_instance.get('POINTS', [])

    if len(points) == 0:
      coords = "NONE"
    elif isinstance(points[0], int):
      coords = "TLBR"
    elif isinstance(points[0], list):
      coords = "POINTS"
    else:
      crt_msg = "WARNING! Cannot deduce coords type given the points {} in plugin `{}`, stream `{}`. Dropping instance".format(points, plugin_signature, stream_name)
      self.__append_exception_message(messages, crt_msg, config_instance)
      is_good_instance = False
      coords = None
    #endif

    if coords == 'TLBR':
      if len(points) != 4:
        crt_msg = "WARNING! 'POINTS' bad configured for COORDS=TLBR in plugin `{}`, stream `{}`. Dropping instance".format(plugin_signature, stream_name)
        self.__append_exception_message(messages, crt_msg, config_instance)
        is_good_instance = False
      elif None in points:
        crt_msg = "WARNING! 'POINTS' bad configured for COORDS=TLBR (it contains None) in plugin `{}`, stream `{}`. Dropping instance".format(plugin_signature, stream_name)
        self.__append_exception_message(messages, crt_msg, config_instance)
        is_good_instance = False
      #endif
    #endif

    if coords == 'POINTS':
      if not all([len(x) == 2 for x in points]):
        crt_msg = "WARNING! 'POINTS' bad configured for COORDS=POINTS in plugin `{}`, stream `{}`. Dropping instance".format(plugin_signature, stream_name)
        self.__append_exception_message(messages, crt_msg, config_instance)
        is_good_instance = False
      elif None in self.log.flatten_2d_list(points):
        crt_msg = "WARNING! 'POINTS' bad configured for COORDS=POINTS (it contains None) in plugin `{}`, stream `{}`. Dropping instance".format(plugin_signature, stream_name)
        self.__append_exception_message(messages, crt_msg, config_instance)
        is_good_instance = False
      #endif
    #endif

    for msg in messages:
      self._create_notification(
        stream_name=stream_name,
        **msg
      )

    return is_good_instance

  def _keep_plugin_good_instances(self, stream_name, config_plugin):
    """
    Keeps only the good instances inside a plugin configuration

    Parameters:
    ----------
    stream_name: str, mandatory
      the name of the stream where the plugin is configured

    config_plugin: dict, mandatory
      the plugin configuration

    Returns:
    -------
    config_plugin: dict
      the cleaned plugin configuration
    """
    if not self._check_config_plugin(stream_name, config_plugin):
      return

    messages = []
    signature = config_plugin['SIGNATURE']
    lst_config_instances = config_plugin['INSTANCES']
    new_lst_config_instances = []

    lst_instance_ids = []
    for config_instance in lst_config_instances:
      is_good_instance = self._check_config_instance(stream_name, signature, config_instance)
      if is_good_instance:
        instance_id = config_instance['INSTANCE_ID']
        if instance_id in lst_instance_ids:
          crt_msg = "WARNING! Instance id {} already existing for plugin `{}` stream `{}`. Keeping just the first one".format(instance_id, signature, stream_name)
          self.__append_exception_message(messages, crt_msg)
        else:
          lst_instance_ids.append(instance_id)
          new_lst_config_instances.append(config_instance)
        #enif
      #endif
    #endfor

    config_plugin['INSTANCES'] = new_lst_config_instances

    for msg in messages:
      self._create_notification(**msg)

    ### maybe it becomes bad after filtering instances
    if not self._check_config_plugin(stream_name, config_plugin):
      return

    return config_plugin
  
  
  def maybe_add_mandatory_plugin_to_stream(self, dct_config_stream):
    """
    Add default mandatory plugin to stream if the plugin does not already exists 
    and if stream requires it.

    Parameters
    ----------
    dct_config_stream : dict
      the pipeline config.

    Returns
    -------
    None.

    """
    # direct connect to config startup
    dct_capture_env = self.owner.cfg_capture_environment
    # get default
    default = dct_capture_env.get(ct.CONFIG_STREAM.DEFAULT_PLUGIN, True)
    # det and overwrite from given stream
    def_sig = dct_config_stream.get(ct.CONFIG_STREAM.DEFAULT_PLUGIN, default)
    # below check code is written in "explicit" mode
    if def_sig is None:
      # no valid default signature
      return
    elif isinstance(def_sig, bool):
      if def_sig:
        def_sig = ct.CONFIG_STREAM.DEFAULT_PLUGIN_SIGNATURE
      else:
        return
    elif isinstance(def_sig, str):
      if len(def_sig) <= 1:  
        # obviously invalid signature
        return
    else:
      # unknown value
      return
    default_plugin_config = dct_config_stream.get(ct.CONFIG_STREAM.DEFAULT_PLUGIN_CONFIG, {})
    stream_name = dct_config_stream[ct.NAME]
    stream_type = dct_config_stream[ct.TYPE].upper()
    additional_config = {}
    if stream_type in ct.CONFIG_STREAM.NO_DATA_STREAMS:
      additional_config = {
        ct.BIZ_PLUGIN_DATA.ALLOW_EMPTY_INPUTS : True,
        ct.BIZ_PLUGIN_DATA.RUN_WITHOUT_IMAGE  : True,
        ct.BIZ_PLUGIN_DATA.PROCESS_DELAY      : 0.5,
      }
    lst_config_plugins = dct_config_stream.get(ct.PLUGINS, [])
    lst_signatures = [x[ct.SIGNATURE].upper() for x in lst_config_plugins]
    has_signature = def_sig.upper() in lst_signatures
    if not has_signature:
      self.P("Adding mandatory plugin '{}' to stream '{}'".format(def_sig, stream_name))
      if not isinstance(dct_config_stream.get(ct.PLUGINS), list):
        dct_config_stream[ct.PLUGINS] = []
      short = self.log.get_short_name(def_sig)
      dct_config_stream[ct.PLUGINS].append({
        ct.SIGNATURE : def_sig, # signature of default plugin - a custom exec type
        ct.INSTANCES : [{
          ct.BIZ_PLUGIN_DATA.INSTANCE_ID                 : short + '_default',
          # ct.BIZ_PLUGIN_DATA.MAX_INPUTS_QUEUE_SIZE       : 1, # should be 1 or more ?
          **additional_config,
          **default_plugin_config
        }]
      })
    return
      
  
  def _get_plugin_instance(self, stream_name, signature, instance_id):
    """
    Returns the plugin instance configuration given the stream name, plugin signature and instance id
    """
    config_stream = self.dct_config_streams.get(stream_name)
    if config_stream is not None:
      lst_config_plugins = config_stream.get('PLUGINS', [])
      for config_plugin in lst_config_plugins:
        if config_plugin['SIGNATURE'].upper() == signature.upper():
          lst_instances = config_plugin['INSTANCES']
          for instance in lst_instances:
            if instance['INSTANCE_ID'] == instance_id:
              return instance
    return
          
      

  def _keep_stream_good_plugins(self, config_stream):
    """
    Keeps only the good plugins inside a stream configuration

    Parameters:
    ----------
    config_stream: dict, mandatory
      the stream configuration

    Returns:
    -------
    config_stream: dict
      the cleaned stream configuration
    """
    if not self._check_config_stream(config_stream):
      return

    messages = []

    new_lst_config_plugins = []
    stream_name = config_stream[ct.CONFIG_STREAM.NAME]
    pipeline_type = config_stream[ct.CONFIG_STREAM.TYPE]
    allowed_plugins = config_stream.get(ct.CONFIG_STREAM.ALLOWED_PLUGINS, [])
    has_allowed_plugins = len(allowed_plugins) > 0
    lst_config_plugins = config_stream.get(ct.CONFIG_STREAM.PLUGINS, [])

    lst_plugin_signatures = []
    for config_plugin in lst_config_plugins:
      new_config_plugin = self._keep_plugin_good_instances(stream_name, config_plugin)
      if new_config_plugin is not None:
        plugin_signature = new_config_plugin[ct.PAYLOAD_DATA.SIGNATURE]
        if has_allowed_plugins and plugin_signature not in allowed_plugins:
          crt_msg = "WARNING! plugin `{}` not allowed for pipeline `{}` ({}). Dropping plugin".format(
            plugin_signature, stream_name, pipeline_type
          )
          self.P(crt_msg, color='error')
          self.__append_exception_message(messages, crt_msg)
          continue
        #endif allowed plugins section
        
        if plugin_signature in lst_plugin_signatures:
          for existing in lst_config_plugins:
            if existing[ct.PLUGIN_INFO.SIGNATURE] == plugin_signature:
              break
          instances = [x[ct.PLUGIN_INFO.INSTANCE_ID] for x in existing[ct.CONFIG_STREAM.INSTANCES]]
          new_instances = [x[ct.PLUGIN_INFO.INSTANCE_ID] for x in new_config_plugin[ct.CONFIG_STREAM.INSTANCES]]
          crt_msg = "WARNING! plugin `{}`:{} already exists for stream `{}` with instances: {}. Keeping just the first one:\n{}".format(
            plugin_signature, new_instances, stream_name, instances,
            lst_config_plugins,
          )
          self.__append_exception_message(messages, crt_msg)
        else:
          lst_plugin_signatures.append(plugin_signature)
          new_lst_config_plugins.append(new_config_plugin)
        #endif existing plugin or new plugin
      #endif plugin valid
    #endfor

    config_stream[ct.CONFIG_STREAM.PLUGINS] = new_lst_config_plugins

    for msg in messages:
      self._create_notification(
        initiator_id=config_stream.get(ct.PAYLOAD_DATA.INITIATOR_ID),
        session_id=config_stream.get(ct.PAYLOAD_DATA.SESSION_ID),
        stream_name=stream_name,
        **msg
      )

    if not self._check_config_stream(config_stream):
      return
    
    self.log.P("Config manager preparing to start stream '{}' with following plugins: {}".format(
      stream_name, lst_plugin_signatures), color=ct.COLORS.DCT,
    )

    return config_stream

  def keep_good_stream(self, config_stream):
    """
    Determines whether a stream configuration is good or not; if it is good, then the method cleans it and
    finally adds default information/data to the stream.
    Public method to be called when an individual stream comes in the box.
    

    Parameters:
    -----------
    config_stream: dict, mandatory
      the stream configuration

    Returns:
    -------
    None or config_stream: dict
    """
    is_good_stream = self._check_config_stream(config_stream)
    if not is_good_stream:
      return

    config_stream = self._keep_stream_good_plugins(config_stream)
    
    
    self.maybe_add_mandatory_plugin_to_stream(dct_config_stream=config_stream)
    return config_stream

  def keep_all_good_streams(self, dct_config_streams):
    """
    Takes all streams configurations and calls `self.keep_good_stream` on them.
    Public method to be called when an multiple stream comes in the box.

    Parameters:
    ----------
    dct_config_streams: dict, mandatory
      key: value where each key is the name of a stream and value is the stream configuration

    Returns:
    --------
    new_dct_config_streams: dict
      The new streams after cleaning and filtering
    """
    self.log.start_timer('keep_all_good_streams')
    new_dct_config_streams = {}
    for stream_name, config_stream in dct_config_streams.items():
      new_config_stream = self.keep_good_stream(config_stream)
      if new_config_stream is not None:
        new_dct_config_streams[stream_name] = new_config_stream
      #endif
    #endfor

    self.log.stop_timer('keep_all_good_streams', skip_first_timing=False)
    return new_dct_config_streams
