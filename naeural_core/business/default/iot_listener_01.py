from naeural_core.business.base import BasePluginExecutor as BaseClass

_CONFIG = {
  **BaseClass.CONFIG,

  'ALLOW_EMPTY_INPUTS': False,

  'VALIDATION_RULES': {
    **BaseClass.CONFIG['VALIDATION_RULES'],
  },
}


class IotListener01Plugin(BaseClass):
  def message_prefix(self, struct_data):
    event_type = struct_data.get('EE_EVENT_TYPE', None)
    node_addr = struct_data.get('EE_SENDER', 'MISSING_ADDRESS')
    node_id = struct_data.get('EE_ID', 'MISSING_ID')
    pipeline = struct_data.get('PIPELINE', None)
    signature = struct_data.get('SIGNATURE', None)
    instance_id = struct_data.get('INSTANCE_ID', None)

    return f"{event_type} event from <{node_id}:{node_addr}> on route [{pipeline}, {signature}, {instance_id}]"

  def process(self):
    struct_data = self.dataapi_struct_datas()

    self.P(f"Received {len(struct_data)} events.", boxed=False)

    for idx, event in struct_data.items():
      keys = list(event.keys())
      prefix = self.message_prefix(event)

      msg = f"\t{idx}. {prefix} containing {len(keys)} keys: {keys}."
      # msg += f" Detailed data:\n{struct_data}"
      self.P(msg, boxed=False)
    # endfor events

    return


