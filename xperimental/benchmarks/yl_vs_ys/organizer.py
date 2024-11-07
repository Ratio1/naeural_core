import os
import shutil


def delete_directory(directory_path):
  if os.path.exists(directory_path):
    shutil.rmtree(directory_path)
    print(f"Deleted {directory_path}")
  else:
    print(f"Directory {directory_path} does not exist.")
  return


USELESS_PLUGINS = [
  'VIEW_SCENE',
  'REST_CUSTOM_EXEC'
]


def useless_dir(directory_name):
  for plugin in USELESS_PLUGINS:
    if plugin in directory_name:
      return True
  return False


def relevant_dir(directory_name):
  return dir_name.startswith('__') and 'archive_output' not in directory_name


def extract_ts_from_path(path):
  ts_str = path.split('_')[0]
  return ts_str


root_dir = r'C:\repos\edge-node\_local_cache\_output'
# res = 'lowres_take7_raise055-050'
res = 'lowres_640x1152_take6_065_050'
res_dest = 'highres_take2'
start_ts = '20240228120347000000'


if __name__ == '__main__':
  dir_list = []
  # Clearing the output folder
  for dir_name in os.listdir(root_dir):
    if os.path.isdir(os.path.join(root_dir, dir_name)):
      abs_dir = os.path.join(root_dir, dir_name)
      if useless_dir(dir_name):
        delete_directory(abs_dir)
      else:
        if relevant_dir(dir_name):
          dir_list.append(abs_dir)
        # end if good dir
      # end if remaining dir
    # end for dirs
  # end for os.walk

  if True:
    for dir_name in dir_list:
      os.makedirs(os.path.join(dir_name, res), exist_ok=True)
      for path in os.listdir(dir_name):
        if os.path.isfile(os.path.join(dir_name, path)):
          abs_path = os.path.join(dir_name, path)
          new_path = os.path.join(dir_name, res, path)
          shutil.move(abs_path, new_path)
        # end if file
      # end for path
    # end for dir_name

  if False:
    for dir_name in dir_list:
      curr_dir = os.path.join(dir_name, res)
      os.makedirs(os.path.join(dir_name, res_dest), exist_ok=True)

      for path in os.listdir(curr_dir):
        if os.path.isfile(os.path.join(curr_dir, path)):
          abs_path = os.path.join(curr_dir, path)
          ts = extract_ts_from_path(path)
          new_path = os.path.join(dir_name, res_dest, path)
          if ts > start_ts:
            shutil.move(abs_path, new_path)
          # endif move
        # end if file
      # end for path
    # end for dir_name

