from naeural_core import Logger

if __name__ == '__main__':
  
  STAGE = 4
  
  l = Logger('ZIP', base_folder='.', app_folder='_local_cache')
  
  logs = l.get_log_files()
  
  if STAGE == 1:
    l.add_file_to_zip('core/xperimental/zip/test1.zip', logs[0])
    
  
  if STAGE == 2:
    l.add_files_to_zip('core/xperimental/zip/test1.zip', logs[1:3])
  
  
  if STAGE == 3:
    l.add_files_to_zip('core/xperimental/zip/test2.zip', logs[:5])


  if STAGE == 4:
    l.add_files_to_zip('core/xperimental/zip/test2.zip', logs[5:10])
