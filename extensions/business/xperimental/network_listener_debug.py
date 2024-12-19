from naeural_core.business.base.network_processor import NetworkProcessorPlugin

_CONFIG = {
  **NetworkProcessorPlugin.CONFIG,

  'NUMBER_OF_PAYLOADS': 5,
  'PAYLOAD_PERIOD': 5,

  'VALIDATION_RULES' : {
    **NetworkProcessorPlugin.CONFIG['VALIDATION_RULES'],
  },
}


class NetworkListenerDebugPlugin(NetworkProcessorPlugin):
  _CONFIG = _CONFIG
  def get_payload_dict(self):
    return {
      'AA_network_debug_counter': self.total_payload_count,
      'AA_network_debug_sender': self.ee_id,
      'AA_network_debug_sender_full': '|'.join([self.ee_id, self._stream_id, self.get_instance_id()]),
      'AA_network_debug_path': (self.ee_id, self.ee_addr, self._stream_id, self.get_instance_id())
    }

  @NetworkProcessorPlugin.payload_handler
  def on_payload_debug(self, data: dict):
    payload_number = data.get('AA_network_debug_counter', -1)
    payload_sender = data.get('AA_network_debug_sender', 'unknown')
    payload_sender_full = data.get('AA_network_debug_sender_full', 'unknown')
    self.P(f'Payload number {payload_number} received from {payload_sender_full}.')
    return

  def process(self):
    if self.time() - self.last_payload_time > self.cfg_payload_period:
      for i in range(self.cfg_number_of_payloads):
        self.add_payload_by_fields(**self.get_payload_dict())
      # endfor each payload
    # endif time to add payloads
    return
