
from naeural_core import Logger
if __name__ == '__main__':
  l = Logger("PLG", base_folder='.', app_folder='_local_cache')
  paths = l.get_all_subfolders("naeural_core/xperimental/numpy", as_package=True)
  for path in paths:
    l.P(path)