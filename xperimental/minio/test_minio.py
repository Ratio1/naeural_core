def minio_download(local_filename,
                   endpoint, 
                   access_key, 
                   secret_key, 
                   bucket_name, 
                   object_name):
  from minio import Minio
  from minio.commonconfig import GOVERNANCE

  try:
    client = Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
        )
    
    res = client.fget_object(
      bucket_name=bucket_name, 
      object_name=object_name, 
      file_path=local_filename,
      )
  except Exception as e:
    print(e) # modifiy in self.P(e, color='error')
    return None  

  return local_filename

def minio_upload(file_path, 
                 endpoint, 
                 access_key, 
                 secret_key, 
                 bucket_name, 
                 object_name=None,
                 days_retention=None,
                 debug=False,
                 ):
  from minio import Minio
  from minio.commonconfig import GOVERNANCE
  from minio.retention import  Retention 
  from datetime import datetime as dttm
  from datetime import timedelta as tdelta
  from uuid import uuid4
  
  if object_name is None:
    object_name = "OBJ_"+ str(uuid4()).upper().replace('-','')
  
  try:
    client = Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
        )
    
    retention = None
    if days_retention is None:
      date = dttm.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0,
        ) + tdelta(days=days_retention)
      retention = Retention(GOVERNANCE, date)
        
    result = client.fput_object(
      file_path=file_path,
      bucket_name=bucket_name,
      object_name=object_name,
      retention=retention,
      )
    
    url = client.presigned_get_object(
      bucket_name=result.bucket_name, 
      object_name=result.object_name,
      )
    
    print("Uploaded '{}' as '{}'".format(file_path, url))
  except Exception as e:
    print(e) # modifiy in self.P(e, color='error')
    return None
  
  return url, object_name
  
if __name__ == '__main__':
  
  fn = 'xperimental/_images/H480_W640/faces1.jpg'
  endpoint = 'MINIO_ENDPOINT'
  access_key = 'MINIO_ACCESS_KEY'
  secret_key = 'MINIO_SECRET_KEY'
  bucket_name ="MINIO_BUCKET_NAME"
  object_name = None
  days_retention = 5
  
  
  url, object_name = minio_upload(
    file_path=fn, 
    endpoint=endpoint, 
    access_key=access_key, 
    secret_key=secret_key, 
    bucket_name=bucket_name, 
    days_retention=days_retention,
    )
  
  down = minio_download(
    local_filename='xperimental/minio/test.jpg',
    endpoint=endpoint, 
    access_key=access_key, 
    secret_key=secret_key, 
    bucket_name=bucket_name,   
    object_name=object_name,
    )
  
  
  
 