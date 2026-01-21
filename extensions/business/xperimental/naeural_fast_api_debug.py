import builtins

from naeural_core.constants import SUPERVISOR_MIN_AVAIL_PRC

from naeural_core.business.default.web_app.naeural_fast_api_web_app import NaeuralFastApiWebApp

_CONFIG = {
  **NaeuralFastApiWebApp.CONFIG,

  # Only for the debug plugin
  "ASSETS": "extensions/business/xperimental",
  "DEBUG_MODE": False,
  "REQUEST_TIMEOUT": 5,

  'VALIDATION_RULES': {
    **NaeuralFastApiWebApp.CONFIG['VALIDATION_RULES'],
  },
}


class NaeuralFastApiDebugPlugin(NaeuralFastApiWebApp):
  """
  Debug plugin class for the Naeural Fast API Web App interface.
  """
  CONFIG = _CONFIG
  @NaeuralFastApiWebApp.endpoint(method="post")
  def test_malicious_file(self, subdir: str = None, filename: str = None):
    res_path = self.diskapi_save_file_to_output(
      subdir=subdir or './_bin',
      filename=filename or 'text.json',
      data="asjegbisudhguisfogbdg"
    )
    return {
      "res_path": res_path,
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def test_request_timeout(self, timeout_s: int = 10):
    self.P(f"Starting sleep for {timeout_s} seconds...", color='y')
    self.sleep(timeout_s)
    self.P(f"Finished sleep for {timeout_s} seconds.", color='g')
    return {
      "status": "success",
      "slept_for": timeout_s
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def test_llama_cpp(self):
    response = {}
    try:
      from llama_cpp import Llama
      response['import'] = True

      cache_dir = self.log.get_models_folder()
      model_params = {
        'n_ctx': 4096,
        'seed': 42,
        'n_batch': 512,
      }

      model = Llama.from_pretrained(
        repo_id="mradermacher/DatA-SQL-1.5B-i1-GGUF",
        filename="DatA-SQL-1.5B.i1-Q4_K_M.gguf",
        cache_dir=cache_dir,
        **model_params,
      )
      response['model_init'] = True

      messages = [
        {
          "role": "system",
          "content": "You are a well known baker."
        },
        {
          "role": "user",
          "content": "Name 3 french pastries and describe why they piss you off."
        }
      ]
      result = model.create_chat_completion(
        messages=messages,
        temperature=0.5
      )
      response["model_inference"] = True
      response["result"] = result

      del model

      response['success'] = True
    except Exception as e:
      response['error'] = str(e)
    return response


    return
  # @NaeuralFastApiWebApp.endpoint(method='post')
  # def command_node(self, node_addr: str):
  #   self.cmdapi_start_pipeline(
  #     node_address=node_addr,
  #     config={
  #       "NAME": "empty_pipeline",
  #       "TYPE": "VOID"
  #     }
  #   )
  #   return

  @NaeuralFastApiWebApp.endpoint(method='post')
  def post_stuff(self, stuff1: str = "", stuff2: str = ""):
    """
    Example of a POST endpoint that receives two strings and returns them in a dictionary.
    """
    self.P(f"Received `{stuff1 = }` and `{stuff2 = }`", color='b')
    stuff1 = stuff1 or {"empty": True}
    stuff2 = stuff2 or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff1': stuff1,
      'Stuff2': stuff2
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def post_stuff_with_extras(self, stuff1: str = "", stuff2: str = "alabala", **kwargs):
    """
    Example of a POST endpoint that receives two strings and returns them in a dictionary.
    """
    self.P(f"Received `{stuff1 = }` and `{stuff2 = }` and ", color='b')
    stuff1 = stuff1 or {"empty": True}
    stuff2 = stuff2 or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff1': stuff1,
      'Stuff2': stuff2,
      'Extra': self.deepcopy(kwargs)
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def post_required_with_extras(self, param1: str, param2: int, **kwargs):
    self.P(f"Received `{param1 = }` | `{param2 = }` and extras = {kwargs}", color='b')
    return {
      'Status': 'Success',
      'Param1': param1,
      'Param2': param2,
      'Extra': self.deepcopy(kwargs)
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def post_required_without_extras(self, param1: str, param2: int):
    self.P(f"Received `{param1 = }` | `{param2 = }`", color='b')
    return {
      'Status': 'Success',
      'Param1': param1,
      'Param2': param2,
    }

  def self_assessment(self):
    """
    Perform self-assessment throughout the epoch to know the local availability so far and
    to predict the final availability of the node at the end of the epoch.
    """
    SUPERVISOR_MIN_AVAIL_PRC = 0.98

    total_seconds_availability, total_seconds_from_start = self.netmon.epoch_manager.get_current_epoch_availability(
      return_absolute=True,
      return_max=True
    )
    total_epoch_seconds = self.netmon.epoch_manager.epoch_length
    prc_node_availability = total_seconds_availability / total_epoch_seconds
    prc_max_availability = total_seconds_from_start / total_epoch_seconds
    prc_missed_availability = prc_max_availability - prc_node_availability
    prc_predicted_availability = 1 - prc_missed_availability
    will_participate = prc_predicted_availability >= SUPERVISOR_MIN_AVAIL_PRC
    comparing_str = f"{'>=' if will_participate else '<='} {SUPERVISOR_MIN_AVAIL_PRC:.2%}"
    comparing_str += f" => {'will' if will_participate else 'will not'} participate in the sync process."
    log_str = f"Current self-assessment:\n"
    log_str += f"\tNode current availability: {prc_node_availability:.2%}\n"
    log_str += f"\tPassed from epoch: {prc_max_availability:.2%}\n"
    log_str += f"\tMissed availability so far: {prc_missed_availability:.2%}\n"
    log_str += f"\tPredicted availability at the end of epoch: {prc_predicted_availability:.2%}{comparing_str}\n"
    self.P(log_str, color='g')
    return


  @NaeuralFastApiWebApp.endpoint(method='get', require_token=True)
  def get_stuff_with_token(self, token: str, stuff: str = ""):
    self.self_assessment()
    if token not in ['123', 'alabala']:
      return "Unauthorized token"
    stuff = stuff or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff': stuff
    }

  @NaeuralFastApiWebApp.endpoint(method='get')
  def get_stuff_with_extras(self, stuff: str = None, **kwargs):
    self.self_assessment()
    stuff = stuff or {"empty": True}
    self.P(f"Received extras: {kwargs}", color='b')

    return {
      'Status': 'Success',
      'Stuff': stuff,
      'Extra': self.deepcopy(kwargs)
    }

  @NaeuralFastApiWebApp.endpoint(method='post')
  def trigger_exception(self, exc_type: str = "ValueError", message: str = "debug crash"):
    """
    Raise a user-selected exception to exercise FastAPI/IPC error handling paths.

    Predefined exception names you can use: ValueError, RuntimeError, KeyError, TypeError, AssertionError, ZeroDivisionError.
    """
    predefined = {
      "ValueError": ValueError,
      "RuntimeError": RuntimeError,
      "KeyError": KeyError,
      "TypeError": TypeError,
      "AssertionError": AssertionError,
      "ZeroDivisionError": ZeroDivisionError,
    }
    exc_cls = predefined.get(exc_type) or getattr(builtins, exc_type, RuntimeError)
    raise exc_cls(message)

  @NaeuralFastApiWebApp.endpoint(method='get')
  def get_stuff_without_token(self, stuff: str = ""):
    stuff = stuff or {"empty": True}
    return {
      'Status': 'Success',
      'Stuff': stuff
    }

