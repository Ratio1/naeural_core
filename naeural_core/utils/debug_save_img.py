try:
  import cv2
except:
  pass
import os
import zipfile
from time import time


def save_images_and_payload_to_output(log, 
                                      np_witness_img, 
                                      np_orig_img, 
                                      path=None,
                                      relative_path=None,
                                      dct_payload=None, 
                                      file_system_manager=None,
                                      last_archive_time=None,
                                      archive_each_minutes=60,
                                      upload_nr_imgs=None, 
                                      perform_upload=False
                                      ):
  if path is not None:
    save_path = path
  elif relative_path is not None:
    save_path = os.path.join(log.get_target_folder('output'), relative_path)
  else:
    raise ValueError('Must specify `path` or `relative_path`')

  dt_now = log.now_str()

  path_witness = os.path.join(save_path, '{}_witness.jpg'.format(dt_now))
  path_img = os.path.join(save_path, '{}_img.jpg'.format(dt_now))
  path_dct = os.path.join(save_path, '{}_dct.json'.format(dt_now))
  path_zip = os.path.join(save_path, f'{dt_now}{"_" + relative_path if relative_path is not None else ""}_archive.zip')

  if np_witness_img is not None:
    if isinstance(np_witness_img, list):
      for i, np_witness_img_shard in enumerate(np_witness_img):
        if np_witness_img_shard is not None:
          path_witness = os.path.join(save_path, '{}_witness_{}.jpg'.format(dt_now, i))
          cv2.imwrite(
            filename=path_witness,
            img=np_witness_img_shard[:, :, ::-1]  # flip channels to BGR
          )
      # endfor
    else:
      cv2.imwrite(
        filename=path_witness,
        img=np_witness_img[:, :, ::-1]  # flip channels to BGR
      )
  # endif

  if np_orig_img is not None:
    cv2.imwrite(
      filename=path_img,
      img=np_orig_img[:, :, ::-1] ### flip channels to BGR
    )
  #endif

  if dct_payload is not None:
    log.save_json(dct_payload, path_dct)
  
  archive_time = None
  
  # upload either if time elapsed or if nr of images is over upload_nr_imgs
  
  # get files
  _lst_files = os.listdir(save_path)
  _lst_files = [
    x for x in _lst_files 
    if ('_witness.jpg' in x) or ('_img.jpg' in x) or ('_dct.json' in x)
  ]
  
  _lst_files = [os.path.join(save_path, x) for x in _lst_files]
  
  n_imgs = len([x for x in _lst_files if '_witness.jpg' in x])

  if (
      (last_archive_time is not None and (time() - last_archive_time) >= (archive_each_minutes * 60)) 
      or 
      (upload_nr_imgs is not None and n_imgs >= upload_nr_imgs)
     ):
    archive_time = time()
    with zipfile.ZipFile(path_zip, 'w') as zipObj:
      for p in _lst_files:
        zipObj.write(p)

    for p in _lst_files:
      # remove all *_witness.jpg, *_img.jpg, *_dct.json files
      os.remove(p)


    url = None
    if perform_upload and file_system_manager is not None:
      url = file_system_manager.upload(
        file_path=path_zip,
        target_path='/DEBUG_SAVE/{}'.format(os.path.basename(path_zip))
      )
    #endif

    if isinstance(dct_payload, dict):
      if url is not None :
        dct_payload['_DBGS_URL'] = url
      else:
        dct_payload['_DBGS_URL'] = path_zip
    #endif

  #endif it is time to archive and maybe upload
  return archive_time