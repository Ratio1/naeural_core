from naeural_core import Logger
from threading import Thread
from time import time
import os

if __name__ == '__main__':

  log = Logger(lib_name='tstp', base_folder='.', app_folder='_local_cache')

  # tested on local files (summing 500mb), use your own file if needed
  fld = '/Users/laurentiupiciu/Downloads/'
  files = ['output_part_000.mkv', 'output_part_001.mkv', 'output_part_002.mkv']

  threads = []

  endpoint = 'MINIO_ENDPOINT'
  access_key = 'MINIO_ACCESS_KEY'
  secret_key = 'MINIO_SECRET_KEY'
  bucket_name ="MINIO_BUCKET_NAME"

  if True:
    # UPLOAD TEST
    start_parallel_up = time()
    for fn in files:
      path = os.path.join(fld, fn)
      t = Thread(target=log.minio_upload, args=(path, endpoint, access_key, secret_key, bucket_name), daemon=True)
      threads.append(t)
      t.start()

    for t in threads:
      t.join()

    """
    # Logs from `log.minio_upload` for each thread
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_001.mkv' as '...' in 13.17s
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_000.mkv' as '...' in 13.18s
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_002.mkv' as '...' in 13.22s
    """
    end_parallel_up = time()

    urls = []
    start_seq_up = time()
    for fn in files:
      path = os.path.join(fld, fn)
      res = log.minio_upload(
        file_path=path, endpoint=endpoint, access_key=access_key,
        secret_key=secret_key, bucket_name=bucket_name
      )
      urls.append(res)
    #endfor

    """
    # Logs for `log.minio_upload` for each call:
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_000.mkv' as '...' in 3.99s
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_001.mkv' as '...' in 5.85s
    #  * Uploaded '/Users/laurentiupiciu/Downloads/output_part_002.mkv' as '...' in 4.61s
    """
    end_seq_up = time()

    log.P("Total time parallel   upload: {:.2f}s".format(end_parallel_up-start_parallel_up))
    log.P("Total time sequential upload: {:.2f}s".format(end_seq_up-start_seq_up))

    """
    [tstp][2022-05-31 18:21:47] Total time parallel   upload: 13.33s
    [tstp][2022-05-31 18:21:47] Total time sequential upload: 14.45s
    """
    print(urls)
  #endif

