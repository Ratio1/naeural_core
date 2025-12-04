from naeural_core import DecentrAIObject

class ServingUtils(DecentrAIObject):
  def __init__(self, **kwargs):
    return

  def get_model_input(self, lst_inputs, input_type="IMG", stream_name="TEST", stream_metadata=None):
    """
    Build the payload expected by ServingManager.predict.

    Parameters
    ----------
    lst_inputs : list
      Items to wrap. They can be raw values (img arrays or structured payloads)
      or pre-built dictionaries containing TYPE / IMG / STRUCT_DATA.
    input_type : str
      One of "IMG" or "STRUCT_DATA". Used when inputs are raw values.
    stream_name : str
      Name of the stream in the payload.
    stream_metadata : dict
      Optional metadata for the stream.
    """
    stream_metadata = stream_metadata or {
      "k1": 0,
      "k2": 1,
    }
    normalized_type = (input_type or "IMG").upper()

    def _wrap_input(x):
      if isinstance(x, dict) and any(k in x for k in ["TYPE", "IMG", "STRUCT_DATA"]):
        return {
          "TYPE": x.get("TYPE", normalized_type),
          "IMG": x.get("IMG"),
          "STRUCT_DATA": x.get("STRUCT_DATA"),
          "INIT_DATA": x.get("INIT_DATA"),
          "METADATA": x.get("METADATA") or {}
        }
      if normalized_type == "STRUCT_DATA":
        return {
          "TYPE": "STRUCT_DATA",
          "IMG": None,
          "STRUCT_DATA": x,
          "INIT_DATA": None,
          "METADATA": {}
        }
      return {
        "TYPE": "IMG",
        "IMG": x,
        "STRUCT_DATA": None,
        "INIT_DATA": None,
        "METADATA": {}
      }

    inputs = [
      {
        "STREAM_NAME": stream_name,
        "STREAM_METADATA": stream_metadata,
        "INPUTS": [_wrap_input(x) for x in lst_inputs]
      }
    ]
    return inputs


if __name__ == '__main__':
  import numpy as np
  from naeural_core import Logger
  log = Logger(
    lib_name='EE_TST',
    base_folder='.',
    app_folder='_local_cache',
    config_file='config_startup.txt',
    max_lines=1000,
    TF_KERAS=False
  )
  su = ServingUtils()
  inputs = su.get_model_input([np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)])
  print(inputs)
