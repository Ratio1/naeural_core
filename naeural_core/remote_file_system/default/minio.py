from naeural_core.remote_file_system.base import BaseFileSystem
from naeural_core import constants as ct

_CONFIG = {
  **BaseFileSystem.CONFIG,
  'VALIDATION_RULES': {
    **BaseFileSystem.CONFIG['VALIDATION_RULES'],
  },
}

class MinioFileSystem(BaseFileSystem):
  """
  configuration example:
    ...
    "CONFIG_UPLOADER" : 
      {
        "ENDPOINT"    : "MINIO_ENDPOINT",
        "ACCESS_KEY"  : "MINIO_ACCESS_KEY",
        "SECRET_KEY"  : "MINIO_SECRET_KEY",
        "BUCKET_NAME" : "MINIO_BUCKET_NAME",
      }
    ...
    
  """

  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    super(MinioFileSystem, self).__init__(**kwargs)
    return

  def upload(self, file_path, target_path, **kwargs):
    dct_minio = {k.lower(): v for k,v in self.config_data.items()}
    
    access_key = kwargs.get('access_key') or dct_minio['access_key']
    secret_key = kwargs.get('secret_key') or dct_minio['secret_key']
    bucket_name = kwargs.get('bucket_name') or dct_minio['bucket_name']
    endpoint = kwargs.get('endpoint') or dct_minio['endpoint']
    secure = kwargs.get('secure') or dct_minio.get('secure', False)
    days_retention = kwargs.get('days_retention') or dct_minio.get('days_retention')
    
    url = self.log.minio_upload(
      file_path=file_path, 
      object_name=target_path,
      endpoint=endpoint, 
      access_key=access_key, 
      secret_key=secret_key, 
      bucket_name=bucket_name, 
      days_retention=days_retention,
      secure=secure,
    )
    return url

  def download(self, uri, local_file_path, **kwargs):
    if uri.startswith('http'):
      return self._http_download(url=uri, local_file_path=local_file_path)

    dct_minio = {k.lower(): v for k,v in self.config_data.items()}

    access_key = kwargs.get('access_key') or dct_minio['access_key']
    secret_key = kwargs.get('secret_key') or dct_minio['secret_key']
    bucket_name = kwargs.get('bucket_name') or dct_minio['bucket_name']
    endpoint = kwargs.get('endpoint') or dct_minio['endpoint']
    secure = kwargs.get('secure') or dct_minio.get('secure', False)

    res = self.log.minio_download(
      local_file_path=local_file_path,
      endpoint=endpoint,
      access_key=access_key,
      secret_key=secret_key,
      bucket_name=bucket_name,
      object_name=uri,
      secure=secure,
    )

    return res

  def list_objects(self, only_names=True, **kwargs):
    dct_minio = {k.lower(): v for k,v in self.config_data.items()}
    
    access_key = kwargs.get('access_key') or dct_minio['access_key']
    secret_key = kwargs.get('secret_key') or dct_minio['secret_key']
    bucket_name = kwargs.get('bucket_name') or dct_minio['bucket_name']
    endpoint = kwargs.get('endpoint') or dct_minio['endpoint']
    secure = kwargs.get('secure') or dct_minio.get('secure', False)

    from minio import Minio
    try:
      client = Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
      )
      lst_objects = list(client.list_objects(bucket_name=bucket_name, recursive=True))
      if only_names:
        lst_objects = list(map(lambda x: x.object_name, lst_objects))
    except:
      lst_objects = []

    return lst_objects
  
if __name__ == '__main__':
  from naeural_core import Logger
  cfg = {
        "ENDPOINT"    : "MINIO_ENDPOINT",
        "ACCESS_KEY"  : "MINIO_ACCESS_KEY",
        "SECRET_KEY"  : "MINIO_SECRET_KEY",
        "BUCKET_NAME" : "MINIO_BUCKET_NAME",
  }
  l = Logger('T', base_folder='.', app_folder='_local_cache')
  ttt = MinioFileSystem(log=l, signature='MINIO', config=cfg)
  ttt.upload('f1','dir/obj', bucket_name='aaa')
