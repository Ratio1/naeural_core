# global dependencies
import os
from time import time

# local dependencies

from naeural_core import Logger

CONFIG_MINIO = {
    "ENDPOINT"   : 'MINIO_ENDPOINT',
    "ACCESS_KEY" : 'MINIO_ACCESS_KEY',
    "SECRET_KEY" : 'MINIO_SECRET_KEY',
    "BUCKET_NAME": "MINIO_BUCKET_NAME"
}

if __name__ == '__main__':
  log = Logger(
    lib_name='SBTST',
    base_folder='.',
    app_folder='_local_cache',
    config_file='config_startup.txt',
    max_lines=1000,
    TF_KERAS=False
  )

  path_fld = r'C:\Users\ETA\Downloads\blur_jobs_new\blur_jobs\9b14f110-6716-448f-a096-357f237bb7e8'

  files = [
    'original_detections.txt'
  ]

  dct_files = {}
  for name in files:
    print('Uploading {}'.format(name))
    start = time()
    url = log.minio_upload(
      file_path=os.path.join(path_fld, name),
      endpoint=CONFIG_MINIO['ENDPOINT'],
      access_key=CONFIG_MINIO['ACCESS_KEY'],
      secret_key=CONFIG_MINIO['SECRET_KEY'],
      bucket_name=CONFIG_MINIO['BUCKET_NAME'],
      object_name='/blur_tests/{}'.format(name),
      debug=True
    )
    stop = time()
    dct_files[name] = {
      'URL': url,
      'TIME': stop - start
    }
  #endfor

  for k, v in dct_files.items():
    log.p('* {}: {}'.format(k, v['URL']))

