from naeural_core.manager import Manager
from naeural_core import Logger
from naeural_core.local_libraries import _ConfigHandlerMixin
from naeural_core import constants as ct
import traceback

class FileSystemManager(Manager, _ConfigHandlerMixin):
  def __init__(self, log : Logger, config, **kwargs):
    self.config = config
    self._dct_file_systems = None
    self._file_system = None
    self.uploader_config = None
    super(FileSystemManager, self).__init__(log=log, prefix_log='[FSYSM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_data = self.config
    self._dct_file_systems = self._dct_subalterns
    return

  @property
  def file_system(self):
    return self._file_system

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_FILE_SYSTEM_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_FILE_SYSTEM_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.LOC_SAFE_FILE_SYSTEM_PLUGINS,
    )

    if _class_def is None:
      msg = "Error loading file system plugin '{}'".format(name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info="No code/script defined for file system plugin '{}' in {}".format(name, ct.PLUGIN_SEARCH.LOC_FILE_SYSTEM_PLUGINS)
      )
    #endif

    return _class_def


  def create_file_system(self):
    """Instantiate the configured remote filesystem backend.

    Resolve the backend from its configured type, give it the uploader
    settings and retain the instance for later upload and download calls.

    Parameters
    ----------
    None

    Returns
    -------
    None
      The manager holds the initialized backend.

    Raises
    ------
    KeyError
      The configuration lacks its type or uploader settings.
    Exception
      Looking up or constructing the configured plugin failed. Initialization
      errors inside the creation block are reported and re-raised.

    Examples
    --------
    >>> manager.create_file_system()  # doctest: +SKIP

    Notes
    -----
    The creation log identifies configuration field names. The backend
    receives the full value mapping.
    """
    # Select the filesystem plugin by the validated configuration type.
    plugin_name = self.config[ct.TYPE]
    # Resolve its implementation before supplying the uploader settings.
    _cls = self._get_plugin_class(plugin_name)
    # Keep the mapping available for later operations on this manager.
    config = self.config['CONFIG_UPLOADER']
    self.uploader_config = config

    try:
      # Log the configured fields without including access credentials.
      self.P("Creating file system manager plugin '{}' with config fields {}".format(
        _cls.__name__, sorted(config)), color='b'
      )

      # Only the plugin needs the original values to configure its backend.
      file_system = _cls(
        log=self.log,
        signature=plugin_name,
        config=config,
      )

      self._file_system = file_system
      self._dct_file_systems[plugin_name] = file_system
    except Exception as exc:
      msg = "Exception '{}' when initializing file system plugin {}".format(exc, plugin_name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
      raise exc
    #end try-except

    return


  def upload(self, file_path, target_path, **kwargs):
    url = None
    try:
      url = self._file_system.upload(file_path=file_path, target_path=target_path, **kwargs)
    except Exception as e:
      msg = "ERROR! Could not upload local '{}' to remote '{}'\n{}".format(file_path, target_path, e)
      self.P(msg)
      self.P(traceback.format_exc(), color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg
      )
    #end try-except

    return url


  def download(self, uri, local_file_path, **kwargs):
    result = None
    try:
      result = self._file_system.download(uri=uri, local_file_path=local_file_path, **kwargs)
    except Exception as e:
      msg = "ERROR! Could not download remote '{}' to local '{}'\n{}".format(uri, local_file_path, e)
      self.P(msg)
      self.P(traceback.format_exc(), color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg
      )
    #end try-except
    return result


  def validate_macro(self):
    file_upload = self.config
    if file_upload is None:
      msg = "'FILE_UPLOAD' is not configured in `config_app.txt`"
      self.add_error(msg)
      return

    plugin_name = file_upload.get('TYPE', None)
    config_uploader = file_upload.get('CONFIG_UPLOADER', None)

    if plugin_name is None:
      msg = "Parameter 'TYPE' is not configured for 'FILE_UPLOAD' in `config_app.txt`"
      self.add_error(msg)
    #endif

    if config_uploader is None:
      msg = "Parameter 'CONFIG_UPLOADER' is not configured for 'FILE_UPLOAD' in `config_app.txt`"
      self.add_error(msg)
    elif not isinstance(config_uploader, dict):
      msg = "Parameter 'CONFIG_UPLOADER' is misconfigured for 'FILE_UPLOAD' in `config_app.txt`. It should be a dictionary"
      self.add_error(msg)

    return
