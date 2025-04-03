import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import string

BASE_API_URL = "https://ratio1-debug.ngrok.dev"
API_URL = f"{BASE_API_URL}/constant_endpoint"
CONCURRENT_REQUESTS = 10000  # adjust based on your machine/API
MAX_WORKERS = 200           # max threads at a time

def make_request(i, random_endpoint=False):
  try:
    current_url = API_URL
    if random_endpoint:
      random_endpoint_name = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
      current_url = f"{BASE_API_URL}/{random_endpoint_name}"
    response = requests.get(current_url)
    return f"Request {i}: {response.status_code}"
  except Exception as e:
    return f"Request {i} failed: {e}"

def run_stress_test():
  start_time = time.time()
  with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = [executor.submit(make_request, i) for i in range(CONCURRENT_REQUESTS)]
    for future in as_completed(futures):
      print(future.result())
  duration = time.time() - start_time
  print(f"\nCompleted {CONCURRENT_REQUESTS} requests in {duration:.2f} seconds")


if __name__ == "__main__":
  run_stress_test()
