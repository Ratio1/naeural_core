import redis
import json

from .. import Logger

class PublisherWrapper:
  def __init__(self,
               log : Logger,
               publisher=None):
    self.log = log
    self.publisher = publisher

  def maybe_publish(self, channel, eid, dct_message, verbose=2):
    if self.publisher is not None:
      message = json.dumps({eid : dct_message})
      self.publisher.publish(channel, message)
      if verbose >= 1:
        self.log.P("Published 1 message through redis channel '{}'".format(channel), color='g')
      if verbose >= 2:
        self.log.P("  Message: {}".format(message))
    return

class Publisher(object):
  def __init__(self, host=None, port=None, db=None, password=None):
    """
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

    self._publisher = redis.Redis(
      host=host, port=port, db=db,
      password=password,
      socket_keepalive=True,
      health_check_interval=10
    )
    return
  
  def publish(self, channel, message):
    """
    Publishes a message through a redis channel

    Parameters:
    -----------
    channel: str, mandatory
      The redis channel through which the message is sent

    message: str, mandatory
      The message to send
    """
    
    # Reset the connection pool everytime to avoid potential timeouts
    self._publisher.connection_pool.reset()
    return self._publisher.publish(channel, message)
    
