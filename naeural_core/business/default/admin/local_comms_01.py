"""

TODO: What is the purpose of LOCAL_COMMS_01 ?


"""

from naeural_core.business.base import BasePluginExecutor

__VER__ = '1.0.0'

_CONFIG = {
  **BasePluginExecutor.CONFIG,

  'LOCAL_COMMS_ENABLED_ON_STARTUP': False,
  'ALLOW_EMPTY_INPUTS': True,

  'VALIDATION_RULES': {
    **BasePluginExecutor.CONFIG['VALIDATION_RULES'],
  },
}


class LocalComms01Plugin(BasePluginExecutor):
  CONFIG = _CONFIG

  def on_init(self):
    self.__comm_manager = self.global_shmem["comm_manager"]
    # state of the local comms
    self.__enabled = False

    # current request: True to enable, False to disable, None to do nothing
    self.__current_request = self.cfg_local_comms_enabled_on_startup

    # local MQTT broker process
    self.__local_mqtt_broker = None
    return

  def on_command(self, data, enable=False, disable=False, **kwargs):
    if (isinstance(data, str) and data.upper() == 'ENABLE') or enable:
      self.__current_request = True
    elif (isinstance(data, str) and data.upper() == 'DISABLE') or disable:
      self.__current_request = False
    return


  def _start_local_mqtt_broker(self):
    import subprocess as sp
    
    # create the config file
    if not self.os_path.exists('mosquitto.conf'):
      with open('mosquitto.conf', 'w') as f:
        f.write("listener 1883\n")
        f.write("allow_anonymous true")
    # endif

    # mosquitto -p 1883 -c mosquitto.conf
    CMD = [
      'mosquitto',
      # '-p', '1883',
      '-c', 'mosquitto.conf'
    ]
    self.P(f"Starting local MQTT broker process via command: {CMD}")
    self.__local_mqtt_broker = sp.Popen(CMD)

    self.P("Local MQTT broker process starting...")

    self.sleep(3)
    # i want to check if the process started and is running
    if self.__local_mqtt_broker.poll() is None:
      self.P("Local MQTT broker process is running.")
      return True

    self.P("Local MQTT broker process is not running.", color='r')
    return False

  def _stop_local_mqtt_broker(self):
    self.P("Local MQTT broker process stopping...")
    self.__local_mqtt_broker.terminate()

    self.sleep(3)

    if self.__local_mqtt_broker.poll() is not None:
      self.__local_mqtt_broker = None
      self.P("Local MQTT broker process stopped.")
      return True

    self.P("Local MQTT broker process is still running. Force stopping it now")
    self.__local_mqtt_broker.kill()

    self.sleep(3)
    if self.__local_mqtt_broker.poll() is not None:
      self.__local_mqtt_broker = None
      self.P("Local MQTT broker process stopped.")
      return True

    self.P("Local MQTT broker process is still running.", color='r')
    return False


  def process(self):
    # if no request, do nothing
    if self.__current_request in [None, False]:
      return

    # if not enabled and request is to enable, enable
    if not self.__enabled and self.__current_request:
      started = self._start_local_mqtt_broker()
      if started:
        self.__comm_manager.enable_local_communication()
        self.__enabled = True

    # if enabled and request is to disable, disable
    if self.__enabled and not self.__current_request:
      self.__comm_manager.disable_local_communication()
      self.__enabled = False
      self._stop_local_mqtt_broker()

    # reset request
    self.__current_request = None
    return
