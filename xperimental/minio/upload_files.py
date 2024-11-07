# global dependencies
import os
from time import time

# local dependencies

from naeural_core import Logger

CONFIG_MINIO = {
    "ENDPOINT"   : 'MINIO_ENDPOINT',
    "ACCESS_KEY" : 'MINIO_ACCESS_KEY',
    "SECRET_KEY" : 'MINIO_SECRET_KEY',
}

if __name__ == '__main__':
  log = Logger(
    lib_name='SBTST',
    base_folder='.',
    app_folder='_cache',
    max_lines=1000,
    TF_KERAS=False
  )


  files = [
    'model-zoo/test_model/test_model_readme.md'
  ]

  for fn in files:
    fn_parts = fn.split('/')
    bucket_name =  fn_parts[0]
    object_name = fn.replace(bucket_name, '')
    log.P("Uploading '{}': Bucket: {}, Object name: {}".format(fn, bucket_name, object_name))
    url = log.minio_upload(
      file_path='xperimental/minio/' + fn,
      endpoint=CONFIG_MINIO['ENDPOINT'],
      access_key=CONFIG_MINIO['ACCESS_KEY'],
      secret_key=CONFIG_MINIO['SECRET_KEY'],
      bucket_name=bucket_name,
      object_name=object_name,
      debug=True
    )

