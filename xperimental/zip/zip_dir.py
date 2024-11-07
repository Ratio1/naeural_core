import zipfile
import os


def zipdir(path, ziph):
  # ziph is zipfile handle
  for root, dirs, files in os.walk(path):
    for file in files:
      ziph.write(os.path.join(root, file),
                 os.path.relpath(os.path.join(root, file),
                                 os.path.join(path, '..')))
  return


def get_archive_idx(archive_name, location):
  lst_dir = os.listdir(location)
  lst_filtered = [os.path.splitext(f)[0] for f in lst_dir if archive_name in f and f.endswith('.zip')]
  lst_idxs = [f[len(archive_name):].split('_')[1] for f in lst_filtered]
  lst_idxs_int = [int(f) for f in lst_idxs if f.isnumeric()]
  return max(lst_idxs_int) + 1 if len(lst_idxs_int) > 0 else 0


if __name__ == '__main__':
  dir_path = 'C:/resources/dummy_ds'
  # dir_path = 'C:/resources/empty_ds'
  archive_name = 'dummy_zip'
  location = 'C:/resources'
  it = get_archive_idx(archive_name=archive_name, location=location)
  archive_path = os.path.join(location, f'{archive_name}_{it}.zip')
  zipf = zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED)
  zipdir(dir_path, zipf)
  zipf.close()

