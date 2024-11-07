import requests

_SERVER = 'https://home.sensibo.com/api/v2'

class SensiboClientAPI(object):
  def __init__(self, api_key):
    self._api_key = api_key

  def _get(self, path, ** params):
    params['apiKey'] = self._api_key
    response = requests.get(_SERVER + path, params=params)
    response.raise_for_status()
    return response.json()

  def _patch(self, path, data, **params):
    params['apiKey'] = self._api_key
    response = requests.patch(_SERVER + path, params=params, data=data)
    response.raise_for_status()
    return response.json()

  def devices(self):
    result = self._get('/users/me/pods', fields='id,room')
    return {x['room']['name']: x['id'] for x in result['result']}

  def pod_measurement(self, pod_uid):
    result = self._get('/pods/{}/measurements'.format(pod_uid))
    return result['result']


if __name__ == "__main__":
  api_key = '0B073b470DeXHoqmXmdeBpVzBbHcLh'
  device_name = "Alex's device"
  
  client = SensiboClientAPI(api_key)
  devices = client.devices()
  print("-" * 10, "devices", "-" * 10)
  print(devices)

  uid = devices[device_name]
  lst_measurements = client.pod_measurement(uid)
  for measurement in lst_measurements:
    for k,v in measurement.items():
      print('{}: {}'.format(k, v))