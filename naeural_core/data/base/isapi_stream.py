from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,

  'CAP_RESOLUTION': 10,
  'LOG_CONNECTION_ERROR_PERIOD': 30,  # seconds

  'ISAPI_CONFIG_METADATA': {
    'MESSAGE_DELIMITER': '--boundary',
    'RESPONSE_AS_JSON': False,
    'XML_SCHEMA': None,  # this should be required only if we read xml's
    'ISAPI_ROUTE': None,
    'CAMERA_IP': None,
    'USERNAME': None,
    'PASSWORD': None,
  },

  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}


class IsapiStreamDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(IsapiStreamDataCapture, self).__init__(**kwargs)
    self._iterator = None
    self.buffer = b''

    self.__last_connection_status_error_time = 0
    return

  def startup(self):
    super().startup()

    self._camera_ip = self.cfg_isapi_config_metadata.get('CAMERA_IP', None)
    self._username = self.cfg_isapi_config_metadata.get('USERNAME', None)
    self._password = self.cfg_isapi_config_metadata.get('PASSWORD', None)
    return

  @property
  def message_delimiter(self):
    delimiter = self.cfg_isapi_config_metadata.get('MESSAGE_DELIMITER', '--boundary')
    return bytes(delimiter, 'utf-8')

  @property
  def isapi_route(self):
    return self.cfg_isapi_config_metadata.get('ISAPI_ROUTE', None)

  @property
  def xml_schema(self):
    return self.cfg_isapi_config_metadata.get('XML_SCHEMA', None)

  @property
  def response_as_json(self):
    return self.cfg_isapi_config_metadata.get('RESPONSE_AS_JSON', False)

  def _init(self):
    self._maybe_reconnect()
    return

  def _maybe_reconnect(self):
    if self.has_connection:
      return

    url = f"http://{self._camera_ip}{self.isapi_route}"
    if self.response_as_json:
      url += '?format=json'

    if self._iterator is None:
      try:
        response = self.requests.get(
          url,
          auth=self.requests.auth.HTTPDigestAuth(self._username, self._password),
          stream=True
        )
        if response is None:
          self.P("Connection fail: remote did not respond", color='r')
          return

        if response.status_code != 200:
          if self.time() - self.__last_connection_status_error_time > self.cfg_log_connection_error_period:
            self.P(f"Connection fail: the status code is {response.status_code}", color='r')
            self.__last_connection_status_error_time = self.time()
          return
        self.__last_connection_status_error_time = 0

        # we use an iterator to read the response byte by byte
        # we no longer use response.iter_lines() because it is not reliable
        self._iterator = response.iter_content(chunk_size=16 * 1024)
        self.buffer = b''

        # this first acquire is to get the first message delimiter
        if self.acquire_full_http_response(parse_response=False) is not None:
          self.has_connection = True
          self.P("Connected to remote")
      except:
        self.P("Connection fail: timeout", color='r')
    return

  def _extract_xml_from_content(self, content, **kwargs):
    """
    Extracts the xml from the content.

    Parameters
    ----------
    content : bytes
        The content to extract the xml from.

    Returns
    -------
    xml : ElementTree
        The xml.
    """

    # we know that the xml starts with <EventNotificationAlert>
    start = 0

    # find the end of the xml
    end = content.find(b'</EventNotificationAlert>')
    if end == -1:
      self.P("Connection fail: end of xml not found", color='r')
      return

    # extract the xml
    xml_content = content[start:end + len('</EventNotificationAlert>\r\n')]

    # replace the xml schema if it is provided
    xml_content = xml_content.replace(bytes(f' xmlns="{self.xml_schema}"', "utf-8"), b'')

    xml = self.ElementTree.fromstring(xml_content)

    return xml

  def _extract_image_from_content(self, content, read_plate=False, **kwargs):
    """
    Extracts the image from the content.

    Parameters
    ----------
    content : bytes
        The content to extract the image from.

    Returns
    -------
    image_plate_tlbr : Tuple[PIL.Image, Tuple[float, float, float, float]|None]
        The image and the plate tlbr.
    """

    # # find the start of the image
    # start == 0 always

    # # find the end of the image
    # end == len(content) - 2 always

    # extract the image
    self.start_timer('anpr_img_extract')
    image_content = content[:-2]

    image = self.BytesIO(image_content)

    pil_image = self.PIL.Image.open(image)
    self.end_timer('anpr_img_extract')

    return pil_image

  def _extract_json_from_content(self, content, **kwargs):
    """
    Extracts JSON from the content.

    Parameters
    ----------
    content : bytes
        The content to extract JSON from.

    Returns
    -------
    object : dict
        The extracted JSON object.
    """

    # TODO(S): test this
    self.P("WARNING: JSON parsing is not tested", color='r')

    # we know that the json starts at the first byte
    start = 0

    # find the end of the json
    end = content.find(b'}')

    # extract the json
    json_content = content[start:end + 1]

    dct_json = self.json.loads(json_content)

    return dct_json

  def extract_object_from_response(self, dct_response, **kwargs):
    """
    Extracts the object from the response.

    Parameters
    ----------
    dct_response : dict
        The response to extract the object from.

    Returns
    -------
    object : ElementTree or Tuple[PIL.Image, Tuple[float, float, float, float]|None]
    """

    content_type = dct_response['content_type']
    content = dct_response['content']

    obj = None

    if b'xml' in content_type:
      self.start_timer('anpr_xml_parse')
      obj = self._extract_xml_from_content(content, **kwargs)
      self.end_timer('anpr_xml_parse')

    elif b'json' in content_type:
      self.start_timer('anpr_json_parse')
      obj = self._extract_json_from_content(content, **kwargs)
      self.end_timer('anpr_json_parse')

    elif b'image/jpeg' in content_type:
      self.start_timer('anpr_img_parse')
      obj = self._extract_image_from_content(content, **kwargs)
      self.end_timer('anpr_img_parse')
    return obj

  def acquire_full_http_response(self, parse_response=True):
    """
    Acquires the full http response.

    Returns
    -------
    dct_response : dict or None
        The parsed response, containing the content type and the content.
    """

    self.start_timer('anpr_http')

    dct_response = None

    index = self.buffer.find(self.message_delimiter)
    if index != -1:
      response = self.buffer[:index]
      dct_response = self._maybe_parse_http_response(response, parse_response=parse_response)
      self.buffer = self.buffer[index + len(self.message_delimiter):]
      self.end_timer('anpr_http')
      return dct_response

    content_len = None
    current_content_len = 0
    lst_byte_chunks = []

    try:
      while True:
        if self._iterator is None:
          return dct_response
        self.start_timer('anpr_http_next')
        byte_chunk = next(self._iterator)
        self.end_timer('anpr_http_next')

        # read bytes until hitting the message delimiter
        # we must make sure we do not skip delimiters split between chunks
        # and so we can update the buffer with the new bytes
        # (this method is not very efficient but it is reliable)

        if content_len is None:
          # break early if we have already found the message delimiter
          index = self.buffer.find(self.message_delimiter)
          if index != -1:
            response = self.buffer[:index]
            dct_response = self._maybe_parse_http_response(response, parse_response=parse_response)
            self.buffer = self.buffer[index + len(self.message_delimiter):] + byte_chunk
            break

          # find the end of the header
          # the header is small so searching from the start is not a big problem
          self.start_timer('anpr_http_header')
          header_end_index = self.buffer.find(b'\r\n\r\n')
          self.end_timer('anpr_http_header')

          if header_end_index == -1:
            # we haven't finished reading the header yet
            self.buffer += byte_chunk
            continue

          # first time computing content_len
          for header in self.buffer.split(b'\r\n'):
            # we know we have the entire header
            if header.startswith(b'Content-Length'):
              content_len = int(header.split(b':')[1].strip())
              # we add the length of the separator to the content length because
              # we determine the end of the content with it
              content_len += len(self.message_delimiter) + len('\r\n\r\n')
            if header.startswith(b'Content-Type'):
              content_type = header.split(b':')[1].strip()
          # endfor

          current_content_len = len(self.buffer[header_end_index + len('\r\n\r\n'):])

        lst_byte_chunks.append(byte_chunk)
        current_content_len += len(byte_chunk)

        if current_content_len >= content_len:
          self.start_timer('anpr_http_join')
          self.buffer += b''.join(lst_byte_chunks)
          self.end_timer('anpr_http_join')
          lst_byte_chunks = []

          index = self.buffer.find(self.message_delimiter, header_end_index + content_len - len(self.message_delimiter))
          # because we added the length of the separator to the content length we should always find the separator
          if index != -1:
            response = self.buffer[:index]
            dct_response = {
              'content_type': content_type,
              'content': response[header_end_index + len('\r\n\r\n'):],
            }
            self.buffer = self.buffer[index + len(self.message_delimiter):]
            break
      # endfor

    except Exception as e:
      self._iterator = None
      self.has_connection = False
      self.P(f"Some error occured: {e}")
    self.end_timer('anpr_http')
    return dct_response

  def _maybe_parse_http_response(self, response, parse_response=True):
    """
    Parses the http response.

    Parameters
    ----------
    response : bytes
        The response to parse.

    Returns
    -------
    dct_response : dict
        The parsed response, containing the content type and the content.
    """

    if not parse_response:
      return response

    content_type = None
    for header in response.split(b'\r\n'):
      if header.startswith(b'Content-Type'):
        content_type = header.split(b':')[1].strip()
        break

    if content_type is None:
      self.P("Connection fail: content type is None", color='r')
      return

    # find the content
    content = None
    start = response.find(b'\r\n\r\n')
    content = response[start + len('\r\n\r\n'):]

    if content is None:
      self.P("Connection fail: content is None", color='r')
      return

    dct_response = {
      'content_type': content_type,
      'content': content,
    }

    return dct_response

  def _run_data_aquisition_step(self):
    self.start_timer('isapi_http')
    dct_response = self.acquire_full_http_response()
    self.end_timer('isapi_http')

    if dct_response is None:
      self.P("Connection fail: remote did not respond", color='r')
      return

    self.start_timer('isapi_get_object')
    obj = self.extract_object_from_response(dct_response)
    self.end_timer('isapi_get_object')

    if b'image' in dct_response['content_type']:
      image = self.np.array(obj)[::-1]
      self._add_img_input(image)

    else:
      self._add_struct_data_input({
        'content_type': dct_response['content_type'],
        'content': obj,
      })

    return

  def _release(self):
    self._iterator = None
    return
