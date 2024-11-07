import asyncio
import uuid
import multiprocessing
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
  def __init__(self, port, auth):
    """
    Initializer for the UvicornPluginComms.

    Parameters
    ----------
    port: int, port value used for commuication with the business plugin
    auth: str, string value used for authenticating and establishing
      communications with the business plugin.
    Returns
    -------
    None
    """
    self.manager = get_client_manager(
      port=port, auth=auth
    )
    self._commands = {}
    self._reads_messages = False
    return

  async def call_plugin(self, *request):
    """
    Calls the business plugin with the supplied method and arguments.

    Parameters
    ----------
    First parameter is the method name of the business plugin that should
    be invoked to process this request. Subsequent parameters are the
    arguments of this method.

    Returns
    -------
    Any - the reply from the business plugin for this request

    Example
    -------
    await comms.call_plugin('foo', 'bar', 'baz') will call the
    business plugin method 'foo' with arguments 'bar' and 'baz'.
    """
    # Generate a uuid for the current message and an event that can be
    # used to signal that the request has been completed.
    event = asyncio.Event()
    ee_uuid = uuid.uuid4()
    ee_uuid = str(ee_uuid)[:8]

    # Add an entry with the current uuid, event and a placeholder for
    # the reply ('value') so that the communication even loop can
    # signal the work being completed.
    self._commands[ee_uuid] = {
      'value' : None,
      'event' : event
    }

    # Send the request to the business plugin.
    self.manager.get_server_queue().put({
      'id' : ee_uuid,
      'value' : request
    })

    if not self._reads_messages:
      # Insert the event processing task into the current even loop if we've
      # not already done that. This needs to be done after the event loop
      # has already been started, which is why we need to do it here.
      self._reads_messages = True
      loop = asyncio.get_running_loop()
      asyncio.ensure_future(self._read_from_plugin(), loop=loop)
    #endif maybe start even loop

    # Yield until we've received a reply back from the business plugin.
    await event.wait()

    # We now have a reply, retrieve the reply and cleanup our entry from the
    # commands dict.
    response = self._commands[ee_uuid]['value']
    del self._commands[ee_uuid]
    return response

  async def _read_from_plugin(self):
    """
    Starts the task of continuously reading messages from the business plugin.
    When a message is recieved, the corresponding request created by
    call_plugin will be signaled to resume and read the reply from the _commands
    dict.

    This is internal to the functioning of the communicator and should NOT be
    directly called by users.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    while True:
      client_queue = self.manager.get_client_queue()
      while not client_queue.empty():
        message = client_queue.get()
        ee_uuid = message['id']
        record = self._commands[ee_uuid]
        record['value'] = message['value']
        record['event'].set()
      #endwhile read all messages from the queue

      # We've done all the work for now. Yield and look again for messages in
      # 0.1 seconds
      await asyncio.sleep(0.001)
    #endwhile communicator loop
    return

