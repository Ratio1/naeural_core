import urllib.request
import ssl
import json
import uuid
import shutil
import os
import traceback
import requests

from naeural_core import Logger
from naeural_core.config.base import BaseConfigRetrievingPlugin

import naeural_core.constants as ct

_CONFIG = {
  **BaseConfigRetrievingPlugin.CONFIG,
  'VALIDATION_RULES': {
    **BaseConfigRetrievingPlugin.CONFIG['VALIDATION_RULES'],
  },
}

class RemoteConfigRetriever(BaseConfigRetrievingPlugin):
  CONFIG = _CONFIG
  def __init__(self, log : Logger,
               **kwargs):
    super(RemoteConfigRetriever, self).__init__(log=log, prefix_log='[RemoteCR]', **kwargs)
    return
  
  def _connect(self, **kwargs):
    self.P("  'Fake' connect - directly accessing url...")
    return

  def _urlopen_config(self, url):
    TIMEOUT = 10
    config_fallback = self.config.get(ct.CONFIG_APP_FALLBACK)
    self.P("  Running '{}'...".format(url))
    try:
      if False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
      ssl._create_default_https_context = ssl._create_unverified_context  
      with urllib.request.urlopen(url, timeout=TIMEOUT) as f:
        content = f.read().decode('utf-8')
        config = json.loads(content)
    except Exception as exc:
      self.P("Failed urllib request:\n{}\nConfig: {}".format(exc, self.config), color='r')
      try:
        resp = requests.get(url, verify=False, timeout=TIMEOUT)
        content = resp.content.decode('utf-8')
        config = json.loads(content)
      except Exception as exc:
        self.P("`requests` also failed after urllib: {}".format(exc), color='r')
        if config_fallback is not None:
          self.P("Using fallback config {}...".format(config_fallback))
          self.P(" File {}".format(
            "is valid." if os.path.isfile(config_fallback) else "DOES NOT exist!"
          ))
          with open(config_fallback, 'rt') as fh:
            config = json.load(fh)
          self.P("  Loaded fallback config.")
        else:
          raise ValueError("Failed urllib request: {}".format(exc))
    return config

  def _get_app_configuration(self, endpoint):
    assert isinstance(endpoint, str)
    return self._urlopen_config(endpoint)

  def _get_streams_configurations(self, endpoint):
    if isinstance(endpoint, str):
      ### streams can be given as a link to a folder with multiple files
      return self.__handle_folder_streams_configurations(endpoint)
    elif isinstance(endpoint, list):
      ### streams can be given as list of links to separate streams configuration files
      return self.__handle_multiple_files_streams_configurations(endpoint)
    elif isinstance(endpoint, dict):
      ### streams can be given as a dictionary where the key is "FOLDERS" and each element is a folder with multiple files
      return self.__handle_multiple_folders_streams_configurations(endpoint)
    else:
      raise ValueError("Unknown value {} for parameter `endpoint`".format(endpoint))

  def __handle_multiple_folders_streams_configurations(self, endpoint):
    folders = endpoint['FOLDERS']
    lst_config_streams = []
    for fld in folders:
      lst_config_streams += self.__handle_folder_streams_configurations(fld)
    return lst_config_streams

  def __handle_folder_streams_configurations(self, endopoint):
    _tmp_fld = str(uuid.uuid4())

    saved, _ = self.log.maybe_download(
      url=endopoint,
      fn=_tmp_fld,
      target='data',
      unzip=True,
      verbose=False,
      print_progress=False
    )

    save_dir = saved[0]

    lst_config_streams = []
    for fn in os.listdir(save_dir):
      if fn.endswith('.txt') or fn.endswith('.json'):
        try:
          lst_config_streams.append(self.log.load_json(
            fname=os.path.join(save_dir, fn),
            folder=None,
            verbose=False
          ))
        except Exception as e:
          self.P("JSON syntactic error with stream {}\n{}".format(fn, e), color='r')

    shutil.rmtree(save_dir)

    return lst_config_streams

  def __handle_multiple_files_streams_configurations(self, endpoint):
    lst_config_streams = []
    for url in endpoint:
      lst_config_streams.append(self._urlopen_config(url))

    return lst_config_streams
