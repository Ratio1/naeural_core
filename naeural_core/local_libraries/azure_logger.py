import json
import requests
import datetime
import hashlib
import hmac
import base64

from multiprocessing.dummy import Pool

class AzureLogger:
  def __init__(self, customer_id, shared_key):
    self.customer_id = customer_id
    self.shared_key = shared_key
    self.pool = Pool(10)

  # Build the API signature
  def build_signature(self, date, content_length, method, content_type, resource):
      x_headers = 'x-ms-date:' + date
      string_to_hash = method + "\n" + str(content_length) + "\n" + content_type + "\n" + x_headers + "\n" + resource
      bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
      decoded_key = base64.b64decode(self.shared_key)
      encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
      authorization = "SharedKey {}:{}".format(self.customer_id,encoded_hash)
      return authorization

  # Build and send a request to the POST API
  def post_data(self, body, log_table):
      method = 'POST'
      content_type = 'application/json'
      resource = '/api/logs'
      rfc1123date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
      content_length = len(body)
      signature = self.build_signature(rfc1123date, content_length, method, content_type, resource)
      uri = 'https://' + self.customer_id + '.ods.opinsights.azure.com' + resource + '?api-version=2016-04-01'

      headers = {
          'content-type': content_type,
          'Authorization': signature,
          'Log-Type': log_table,
          'x-ms-date': rfc1123date
      }
      self.pool.apply_async(
        requests.post,
        args=[uri],
        kwds={
          'data': body,
          'headers': headers
        },
        callback=self.on_success,
        error_callback=self.on_error
      )

  @staticmethod
  def on_success(r: requests.Response):
    if r.status_code == 200:
      print(f'Post succeed: {r}')
    else:
      print(f'Post failed: {r}')

  @staticmethod
  def on_error(ex: Exception):
    print(f'Post requests failed: {ex}')

  @staticmethod
  def parse_carturesti_request(dct_json):
    dct_parsed = {k: str(v) for k, v in dct_json.items()}
    dct_parsed['TimeSent'] = str(datetime.datetime.now())
    return json.dumps(dct_parsed)

if __name__ == '__main__':
  CUSTOMER_ID = '2d2e348a-33e6-40f4-9bf3-8ae72cd961d1'
  SHARED_KEY = 'fkJ7MbNWYlYUUlAX+XDU8VCJXpXi8rNaNSZTm5OD8h8BDEpCYa6ImiqKScef1XAUW74428KZphZ3cl8GHxy3HA=='
  LOG_TABLE = 'carturesti_requests'

  # An example JSON web monitor object
  dct_json = {
    "RECOM_TYPE": "get_short_term_last_minute_recom",
    "START_ITEM": 34,
    "CONTEXT_ID": "Carturesti01",
    "CURRENT_BASKET": [0, 1, 2],
    "NR_ITEMS": 3
  }

  az_log = AzureLogger(customer_id=CUSTOMER_ID, shared_key=SHARED_KEY)
  for i in range(100):
    print("req_sent")
    az_log.post_data(az_log.parse_carturesti_request(dct_json), log_table=LOG_TABLE)