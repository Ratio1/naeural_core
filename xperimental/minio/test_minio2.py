from naeural_core import Logger  

if __name__ == '__main__':
  
  l = Logger('TST', base_folder='.', app_folder='_cache')
  
  endpoint = 'MINIO_ENDPOINT'
  access_key = 'MINIO_ACCESS_KEY'
  secret_key = 'MINIO_SECRET_KEY'
  bucket_name ="MINIO_BUCKET_NAME"
  minio_object_name = 'minio:y5/coco.txt'
  http_object_name = 'https://www.dropbox.com/s/krzygbdl6qkzf8e/coco.txt?dl=1'
  
  for url, fn in zip([minio_object_name, http_object_name],['from_minio.txt', 'from_http.txt']):    
      down = l.maybe_download_model(
        url=url, 
        model_file=fn,
        endpoint=endpoint,
        secret_key=secret_key,
        bucket_name=bucket_name,
        access_key=access_key,
      )
    
    
    
 