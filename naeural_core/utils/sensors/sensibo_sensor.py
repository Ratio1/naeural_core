import requests
from datetime import datetime

from naeural_core.utils.sensors.base import AbstractSensor

_SERVER = 'https://home.sensibo.com/api/v2'

class SensiboSensor(AbstractSensor):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return

  def _setup_connection(self, device_name, api_key, server_addr=_SERVER):
    self._device_name = device_name
    self._api_key = api_key
    self._server_addr = server_addr
    devices = self.__list_devices()
    self._uid = devices[device_name]
    return
  
  def _get_single_observation(self):
    obs = self.__get_last_measurement()
    return obs
    
  def _has_training_data(self):
    return None
    
  def _get_training_data(self):
    return False    
  
  def __get_last_measurement(self):
    res = self.__get_measurement(self._uid)
    return res[-1]  

  def __get_data(self, path, **params):
    params['apiKey'] = self._api_key
    response = requests.get(self._server_addr + path, params=params)
    response.raise_for_status()
    return response.json()

  def __patch(self, path, data, **params):
    params['apiKey'] = self._api_key
    response = requests.patch(self._server_addr + path, params=params, data=data)
    response.raise_for_status()
    return response.json()

  def __list_devices(self):
    result = self.__get_data('/users/me/pods', fields='id,room')
    return {x['room']['name']: x['id'] for x in result['result']}

  def __get_measurement(self, pod_uid=None):
    if pod_uid is None:
      pod_uid = self._uid
    results = self.__get_data('/pods/{}/measurements'.format(pod_uid))
    results = results['result']
    for res in results:
      if 'time' in res:
        str_dt = res['time']['time']
        dt = datetime.strptime(str_dt, '%Y-%m-%dT%H:%M:%S.%fZ')
        delay = res['time']['secondsAgo']
        res['read_time'] = dt
        res['read_time_str']  = str_dt
        res['read_delay'] = delay
        
    return results
    
  
  
if __name__ == "__main__":
  api_key = '0B073b470DeXHoqmXmdeBpVzBbHcLh'
  device_name = "Alex's device"
  
  sensor = SensiboSensor(api_key=api_key,device_name=device_name)
  print(sensor)
  print(sensor._get_single_observation())
