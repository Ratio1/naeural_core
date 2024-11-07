import redis
from abc import abstractmethod
import json
from time import sleep
from copy import deepcopy

NEW_ENTITY = 'NEW'

class AbstractStateSubscriber(object):
  """
  Abstract class used to inherit state subscriber classes employed for
  the publish-subscribe design pattern.

  Works only with redis.

  Public methods:
  - subscribe(channel)
  - get_state(channel, batch_size)
  """

  def __init__(self, host=None, port=None, db=None, password=None):
    """
    Instantiates the redis subscriber object, the channels object (on
    which the message information is stored), and the default values for an empty channel

    Parameters:
    ------------
    host: str, optional
      the redis server host
      Default to None ('localhost')

    port: int, optional
      the redis server port
      Default to None (6379)

    db: int, optional
      the redis server db
      Default to None (0)

    password: str, optional
      The password of the redis server
      Default to None - if the server does not have any password
    """
    if host is None:
      host = 'localhost'

    if port is None:
      port = 6379

    if db is None:
      db = 0

    self.default_value_channel = None
    self._set_default_value_channel()
    self.channels = dict()

    self.redis_ssp = redis.Redis(
      host=host, port=port, db=db,
      password=password,
      health_check_interval=10
    )
    self._subscriber = self.redis_ssp.pubsub()
    return

  @abstractmethod
  def _refresh_messages(self, batch_size=None):
    """
    Abstract method used for iterating through the redis queue and storing the
    published messages

    Parameters:
    -----------
    batch_size: int, optional
      number of maximum messages to be processed one time. Used to handle
      the situation in which the messages are sent faster than the method can handle.
      Default to None


    Implementation info:
    - this method iterates through the message queue, decodes the jsons, and loads them into the
    `self.channels` object
    - If the message queue is too long it only processes the first 'batch_size' messages. This
    is done in order to handle the situation in which the messages are sent faster than the
    method can handle.
    """
    pass

  @abstractmethod
  def _set_default_value_channel(self):
    """
    Abstract method to be implemented in child classes.
    Sets `self.default_value_channel` (the initial value for `self.channels[channel]`)
    to a custom value
    """
    pass

  def subscribe(self, channel):
    """
    Method used for subscribing to a redis channel

    Parameters:
    -----------
    channel: str, mandatory
      the channel to subscribe to
    """
    if channel not in self.channels:
      self.channels[channel] = deepcopy(self.default_value_channel)

    self._subscriber.subscribe(channel, socket_keepalive=True)

  def _clear_state(self, channel):
    """
    Method used to reset the data stored for a given channel

    Parameters:
    ----------
    channel: str, mandatory
      the channel to clear the state
    """
    self.channels[channel] = deepcopy(self.default_value_channel)

  def get_state(self, channel=None, batch_size=None):
    """
    Method used to get the queued messages from a channel. It calls `refresh_messages`
    method, clears the queue and returns the messages

    Parameters:
    -----------
    channel: str, optional
      the channel from which to get the messages from
      Default is None (the first channel found in `channels` object)

    batch_size: int, optional
      number of maximum messages to be processed one time.
      Default is None
    """
    if channel is None:
      if len(self.channels) > 0:
        channel = list(self.channels.keys())[0]

    self._refresh_messages(batch_size=batch_size)
    state = self.channels[channel]
    self._clear_state(channel)
    return state



class SingleMessageStateSubscriber(AbstractStateSubscriber):
  """
  Class defined to implement the logic for the case when each channel receives a single message.

  Public methods:
    - subscribe(channel)
    - get_state(channel, batch_size)
  """

  def _set_default_value_channel(self):
    """
    Sets `self.default_value_channel` to None - each channel receives a single message.

    See `AbstractStateSubscriber._set_default_value_channel` for more docstring
    """
    self.default_value_channel = None

  def _refresh_messages(self, batch_size=None):
    """
    Method used for iterating through the redis queue and storing the published messages.
    See `AbstractStateSubscriber._refresh_messages` for more docstring

    Parameters:
    -----------
    batch_size: int, optional
      number of maximum messages to be processed one time. Used to handle
      the situation in which the messages are sent faster than the method can handle.
      Default to None (1000)
    """

    if batch_size is None:
      batch_size = 1000

    if len(self.channels) == 0:
      return

    for _ in range(batch_size):
      msg = self._subscriber.get_message()

      if msg is None:
        break

      try:
        channel = msg['channel'].decode('utf-8')
        data = msg['data'].decode('utf-8')
      except:
        continue

      self.channels[channel] = data
      sleep(0.01)
    # end - for _ in range(batch_size):

    return


class MultipleMessagesStateSubscriber(AbstractStateSubscriber):
  """
  Class defined to implement the logic for the case when each channel receives multiple messages,
  indexed in a dictionary

  Public methods:
    - subscribe(channel)
    - get_state(channel, batch_size)
  """

  def __init__(self, channel, **kwargs):
    super().__init__(**kwargs)
    self.subscribe(channel)
    self.channels[channel] = deepcopy(self.default_value_channel)
    return

  def _set_default_value_channel(self):
    """
    Sets `self.default_value_channel` to `{}` - each channel receives multiple messages,
    indexed in a dictionary
    """
    self.default_value_channel = {}

  def _refresh_messages(self, batch_size=None):
    """
    Method used for iterating through the redis queue and storing the published messages.
    See `AbstractStateSubscriber._refresh_messages` for more docstring

    Parameters:
    -----------
    batch_size: int, optional
      number of maximum messages to be processed one time. Used to handle
      the situation in which the messages are sent faster than the method can handle.
      Default to None (1000)
    """
    if batch_size is None:
      batch_size = 1000

    if len(self.channels) == 0:
      return

    for _ in range(batch_size):
      msg = self._subscriber.get_message()

      if msg is None:
        break

      try:
        channel = msg['channel'].decode('utf-8')
        data = msg['data'].decode('utf-8')
      except:
        continue

      data = json.loads(
        data,
        object_hook=lambda d: {int(k) if k.isnumeric() else k: v for k, v in d.items()}
      )

      object_id = list(data.keys())[0]
      obj_state = data[object_id]


      if object_id != NEW_ENTITY:
        if object_id not in self.channels[channel]:
          self.channels[channel][object_id] = {}

        for key_state, value_state in obj_state.items():
          self.channels[channel][object_id][key_state] = value_state

      else:
        if object_id not in self.channels[channel]:
          self.channels[channel][object_id] = []

        self.channels[channel][object_id].append(obj_state)
      #endif

      sleep(0.01)
    # end - for _ in range(batch_size):

    return
