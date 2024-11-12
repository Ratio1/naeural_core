"""
Example pipeline:

{
  "NAME" : "IoT-Example",
  "TYPE" : "IoTQueueListener",
  
  "PATH_FILTER" : [
    None, None, 
    ["IOT_PLUGIN_DEMO_1", "IOT_PLUGIN_DEMO_2"], 
    None
  ],
  "MESSAGE_FILTER" : {},
  
  "PLUGINS" : [
    {
      "SIGNATURE" : "IOT_PLUGIN_DEMO_1",
      "INSTANCES" : [
        {
          "INSTANCE_ID" : "IOT_PLUGIN_DEMO_1_INSTANCE_1",
          "USE_TYPE" : "A",
          "PROCESS_DELAY" : 0.1
        }
      ]
    },
    {
      "SIGNATURE" : "IOT_PLUGIN_DEMO_1",
      "INSTANCES" : [
        {
          "INSTANCE_ID" : "IOT_PLUGIN_DEMO_1_INSTANCE_2",
          "USE_TYPE" : "B",
          "PROCESS_DELAY" : 0.1
        }
      ]
    },
    {
      "SIGNATURE" : "IOT_PLUGIN_DEMO_1",
      "INSTANCES" : [
        {
          "INSTANCE_ID" : "IOT_PLUGIN_DEMO_1_INSTANCE_3",
          "USE_TYPE" : "C",
          "PROCESS_DELAY" : 0.1
        }
      ]
    }        
  ]
}


IOT_PLUGIN_DEMO_1:
  process:
    if datainput['USE_TYPE'] != MYTYPE:
      return
    do_the_processing(datainput)

"""


from naeural_core.data.base.base_iot_queue_listener import \
    BaseIoTQueueListenerDataCapture

_CONFIG = {
  **BaseIoTQueueListenerDataCapture.CONFIG,

  'VALIDATION_RULES': {
    **BaseIoTQueueListenerDataCapture.CONFIG['VALIDATION_RULES'],
  },
}


class IoTQueueListenerDataCapture(BaseIoTQueueListenerDataCapture):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(IoTQueueListenerDataCapture, self).__init__(**kwargs)
    return

  def _filter_message(self, unfiltered_message):
    filtered_message = unfiltered_message
    return filtered_message

  def _parse_message(self, filtered_message):
    return filtered_message
