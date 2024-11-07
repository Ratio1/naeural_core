from naeural_core.business.base.drivers.device import Device


class HttpDevice(Device):
  def __init__(self, **kwargs):
    super(HttpDevice, self).__init__(**kwargs)
    self._headers = None
    return

  def device_get(self, path, headers=None, timeout=10):  # todo: prefix device
    """
    Get data from device
    Parameters
    ----------
    path
    headers
    timeout

    Returns
    -------

    """
    return self.__execute_request(method="GET", path=path, headers=headers, timeout=timeout)

  def device_post(self, path, body, headers=None, timeout=10):
    """
    Post data to device
    Parameters
    ----------
    path
    body
    headers
    timeout

    Returns
    -------

    """
    return self.__execute_request(method="POST", path=path, body=body, headers=headers, timeout=timeout)

  def device_put(self, path, body, headers=None, timeout=10):
    """
    Put data to device
    Parameters
    ----------
    path
    body
    headers
    timeout

    Returns
    -------

    """
    return self.__execute_request(method="PUT", path=path, body=body, headers=headers, timeout=timeout)

  def device_delete(self, path, headers=None, timeout=10):
    """
    Delete data from device
    Parameters
    ----------
    path
    headers
    timeout

    Returns
    -------

    """
    return self.__execute_request(method="DELETE", path=path, headers=headers, timeout=timeout)

  def __execute_request(self, method, path, body=None, headers=None, timeout=10):
    """
    Execute request to device
    Parameters
    ----------
    method
    path
    body
    headers
    timeout

    Returns
    -------

    """
    url, dct_headers = self.__get_url_and_headers(headers=headers, path=path)
    try:
      if method.upper() == "GET":
        response = self.requests.get(url=url, headers=dct_headers, timeout=timeout)
      elif method.upper() == "POST":
        response = self.requests.post(url=url, json=body, headers=dct_headers, timeout=timeout)
      elif method.upper() == "PUT":
        response = self.requests.put(url=url, json=body, headers=dct_headers, timeout=timeout)
      elif method.upper() == "DELETE":
        response = self.requests.delete(url=url, headers=dct_headers, timeout=timeout)
      else:
        raise ValueError("Method {} not implemented in Driver API".format(method))
      if response.status_code in [200, 201]:
        if response.text:
          return response.json()
    except Exception as e:
      raise e

  def __get_url_and_headers(self, headers: dict, path: str):
    """Get the URL and headers for the request.

    Parameters
    ----------
    headers : dict
        The headers to use for the request.
    path : str
        The path to append to the base URL.

    Returns
    -------
    tuple
        The URL and headers for the request.
    """
    base_url = self.cfg_device_ip
    if not base_url.startswith(('http://', 'https://')):
      base_url = "http://" + base_url  # we assume http if not provided

    url = base_url + path

    if headers is None:
      headers = self._headers if hasattr(self, '_headers') else {}

    if not isinstance(headers, dict):
      raise TypeError("The headers must be provided as a dictionary")

    return url, headers
