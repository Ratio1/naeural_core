import os


def is_image(file_path):
  return file_path.lower().endswith(('.jpg', '.jpeg', 'png', 'bmp'))


def dir_path_to_dict(path, exclude_dirs=None):
  exclude_dirs = exclude_dirs or []
  files_dict = {}
  for subdir, dirs, files in os.walk(path):
    for file in files:
      curr_path = os.path.join(subdir, file)
      sub_ds_name = os.path.split(subdir)[-1]
      if sub_ds_name in exclude_dirs:
        continue
      if sub_ds_name not in files_dict.keys():
        files_dict[sub_ds_name] = []
      if 'pickle' not in curr_path and is_image(curr_path):
        files_dict[sub_ds_name].append(curr_path)
    # endfor file
  # endfor subdir
  return files_dict

keep_dirs = None
test_ds_path = 'C:\\resources\\all_servings_tests'
files_dict = dir_path_to_dict(test_ds_path, exclude_dirs=keep_dirs)


if __name__ == '__main__':
  print(files_dict)
