import os
from naeural_core import constants as ct


class _ValidateConfigStartupMixin(object):
  def __init__(self):
    super(_ValidateConfigStartupMixin, self).__init__()
    return

  def validate_config_retrieve(self):
    config_retrieve = self.config_data.get('CONFIG_RETRIEVE', None)
    app_config_idx = None
    streams_config_idx = []

    if config_retrieve is None:
      msg = "'{}' is not configured in `config_startup.txt`. The application will start using the default value. Pay attention that this parameter defines the source of the configurations (app and streams).".format('CONFIG_RETRIEVE')
      self.add_warning(msg)
    else:
      if not isinstance(config_retrieve, list):
        msg = "'{}' is not well configured in `config_startup.txt`. The value for it should be List[Dict].".format('CONFIG_RETRIEVE')
        self.add_error(msg)
        return
      #endif

      if len(config_retrieve) == 0:
        msg = "'{}' is not configured in `config_startup.txt`. The application will start using the default value. Pay attention that this parameter defines the source of the configurations (app and streams).".format('CONFIG_RETRIEVE')
        self.add_warning(msg)
      else:
        for idx,elem in enumerate(config_retrieve):
          if not isinstance(elem, dict):
            msg = "'{}' is not well configured in `config_startup.txt`. The value for it should be List[Dict]. Please check '{}'".format('CONFIG_RETRIEVE', elem)
            self.add_error(msg)
          else:
            if 'TYPE' not in elem:
              msg = "'{}' is not well configured in `config_startup.txt`. Each dictionary should have 'TYPE' configured. Please check '{}'".format('CONFIG_RETRIEVE', elem)
              self.add_error(msg)
            #endif

            if 'APP_CONFIG_ENDPOINT' in elem:
              app_config_idx = idx

            if 'STREAMS_CONFIGS_ENDPOINT' in elem:
              streams_config_idx.append(idx)
        #endfor
      #endif
    #endif

    fld_box_config = self.log.get_data_subfolder('box_configuration')
    content_box_config = []
    if fld_box_config is not None:
      content_box_config = os.listdir(fld_box_config)

    if app_config_idx is None and 'config_app.txt' not in content_box_config:
      msg = "No 'APP_CONFIG_ENDPOINT' configured in 'CONFIG_RETRIEVER' in `config_startup.txt` and there is no `config_app.txt` in `_data/box_configuration`."
      self.add_error(msg)

    return
