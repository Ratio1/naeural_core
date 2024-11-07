import pandas as pd
import json

class _DataframeLoaderMixin(object):
  """
  Mixin for loading a csv (decoding it from its json representation or reading it).
  This mixin is used by `plugins.data.a_dummy_dataframe` and `plugins.data.a_dummy_dataframe_map_reduce`
  """
  def __init__(self):
    super(_DataframeLoaderMixin, self).__init__()
    return

  def dataframe_load(self, source=None):
    # receives a dataframe (json representation or path for read_csv) as part 
    # of "STREAM_CONFIG_METADATA" or direclty as "URL"
    if source is None:
      source = self.cfg_stream_config_metadata.get('DF_SOURCE')
    if source is None:
      source = self.cfg_url

    try:
      json_repr = json.loads(source)
    except json.decoder.JSONDecodeError:
      json_repr = None

    if json_repr is not None:
      return pd.DataFrame(json_repr)
    else:
      return pd.read_csv(source)