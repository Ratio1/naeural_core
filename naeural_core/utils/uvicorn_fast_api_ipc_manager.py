import asyncio
import os
import queue
import threading
import time
import uuid
import multiprocessing
import traceback
from multiprocessing.managers import SyncManager

# The following need to be global due to the multiprocessing
# method being set to spawn.
class CommsServerManager(SyncManager):
  pass
class CommsClientManager(SyncManager):
  pass

server_queue = None
def get_server_queue():
  global server_queue
  if server_queue is None:
    server_queue = multiprocessing.Queue()
  return server_queue

client_queue = None
def get_client_queue():
  global client_queue
  if client_queue is None:
    client_queue = multiprocessing.Queue()
  return client_queue


def get_server_manager(auth):
    CommsServerManager.register('get_server_queue', callable=get_server_queue)
    CommsServerManager.register('get_client_queue', callable=get_client_queue)
    manager = CommsServerManager(
      address=('127.0.0.1', 0),
      authkey=auth
    )
    manager.start()
    return manager

def get_client_manager(port, auth):
    CommsClientManager.register('get_server_queue')
    CommsClientManager.register('get_client_queue')
    manager = CommsClientManager(
      address=('127.0.0.1', port),
      authkey=auth
    )
    manager.connect()
    return manager

class UvicornPluginComms:
  """
  Communicator for Uvicorn/FastAPI with an instance of the fastapi plugin.
  Since this is meant to be used on the web server side, it's methods
  are asynchronous and will use the FastAPI even loop to deliver
  messages.

  FastAPI endpoints can call the call_plugin method to deliver and receive
  requests from the associated business plugin.
  """
  def __init__(self, port, auth, timeout_s=120, additional_fastapi_data: dict = None):
    """
    Initializer for the UvicornPluginComms.

    Parameters
    ----------
    port: int, port value used for commuication with the business plugin
    auth: str, string value used for authenticating and establishing
      communications with the business plugin.
    timeout_s: float, timeout in seconds to wait for a response from the business plugin
      before returning a timeout error.
    additional_fastapi_data: dict, additional data to be added to response
    Returns
    -------
    None
    """
    self.manager = get_client_manager(port=port, auth=auth)
    self._server_queue = self.manager.get_server_queue()
    self._client_queue = self.manager.get_client_queue()

    self._commands = {}
    self._reads_messages = False
    self._loop = None

    self._stop_reader = threading.Event()
    self._reader_thread = None

    self._response_timeout_s = timeout_s
    self._additional_fastapi_data = additional_fastapi_data or {}
    return

  async def call_plugin(self, *request, profile=None):
    """
    Calls the business plugin with the supplied method and arguments.

    Parameters
    ----------
    First parameter is the method name of the business plugin that should
    be invoked to process this request. Subsequent parameters are the
    arguments of this method.

    Returns
    -------
    tuple (Any, list[time measurements]), where:
    Any - the reply from the business plugin for this request
    list[time measurements] - list of time measurements taken at
      various points in the call_plugin method for performance
      measurement purposes.

    Example
    -------
    await comms.call_plugin('foo', 'bar', 'baz') will call the
    business plugin method 'foo' with arguments 'bar' and 'baz'.
    """
    event = asyncio.Event()
    ee_uuid = uuid.uuid4().hex

    # Add an entry with the current uuid, event and a placeholder for
    # the reply ('value') so that the communication even loop can
    # signal the work being completed.
    profile_obj = profile if isinstance(profile, dict) else None
    if profile_obj is not None:
      profile_obj.setdefault("req_id", ee_uuid)

    self._commands[ee_uuid] = {
      'value': None,
      'event': event,
      'profile_data': None,
      'profile_obj': profile_obj,
    }

    if not self._reads_messages:
      self._reads_messages = True
      self._loop = asyncio.get_running_loop()
      self._start_reader_thread()

    t_put_wall_ns = time.time_ns() if profile_obj is not None else None
    t_put_start_ns = time.perf_counter_ns() if profile_obj is not None else None
    await asyncio.to_thread(
      self._server_queue.put,
      {
        'id': ee_uuid,
        'value': request,
        'profile': profile_obj,
        't_put_wall_ns': t_put_wall_ns,
      }
    )
    t_put_end_ns = time.perf_counter_ns() if profile_obj is not None else None
    if profile_obj is not None:
      profile_obj["t_put_start_ns"] = t_put_start_ns
      profile_obj["t_put_end_ns"] = t_put_end_ns
      profile_obj["t_put_wall_ns"] = t_put_wall_ns
      profile_obj["t_wait_start_ns"] = time.perf_counter_ns()

    try:
      await asyncio.wait_for(event.wait(), timeout=self._response_timeout_s)
    except Exception as e:
      print(f"[ipc-timeout] req_id={ee_uuid} timeout_s={self._response_timeout_s} exc={type(e).__name__}: {e}")
      self._commands.pop(ee_uuid, None)
      return {
        "status_code": 504,
        "logged": True,
        **self._additional_fastapi_data,
        "result": f"Error waiting for plugin response: {e}",
        "exception_metadata": {
          "exception": str(e),
          "trace": traceback.format_exc(),
          "logged": True,
        }
      }

    # We now have a reply, retrieve the reply and cleanup our entry from the
    # commands dict.
    record = self._commands[ee_uuid]
    response = record['value']

    if record.get("profile_obj") is not None:
      record["profile_obj"]["t_wait_end_ns"] = time.perf_counter_ns()
      response_profile = record.get("profile_data")
      if isinstance(response_profile, dict):
        record["profile_obj"].update(response_profile)
    del self._commands[ee_uuid]
    return response

  def _start_reader_thread(self):
    if self._reader_thread is not None:
      return

    def _reader():
      while not self._stop_reader.is_set():
        try:
          message = self._client_queue.get(True, 0.25)
        except queue.Empty:
          continue
        except Exception as exc:
          print(f"[ipc-reader-error] exc={exc}")
          continue

        if self._loop is None:
          continue
        self._loop.call_soon_threadsafe(self._deliver_message, message)

    self._reader_thread = threading.Thread(
      target=_reader,
      name="UvicornPluginCommsReader",
      daemon=True
    )
    self._reader_thread.start()
    return

  def _deliver_message(self, message):
    try:
      ee_uuid = message.get('id')
      if ee_uuid not in self._commands:
        return
      record = self._commands[ee_uuid]
      record['value'] = message.get('value')
      record['profile_data'] = message.get('profile')
      record['event'].set()
    except Exception as exc:
      print(f"[ipc-deliver-error] req_id={message.get('id')} exc={exc}")
      return

  async def send_profile_event(self, profile):
    if not isinstance(profile, dict):
      return
    await asyncio.to_thread(
      self._server_queue.put,
      {
        "id": None,
        "value": ("__profile__", profile),
      }
    )
    return
