import os, platform, subprocess, re

from naeural_core import Logger

def get_processor_platform():
    if platform.system() == "Windows":
      return platform.processor()
    elif platform.system() == "Darwin":
      os.environ['PATH'] = os.environ['PATH'] + os.pathsep + '/usr/sbin'
      command ="sysctl -n machdep.cpu.brand_string"
      return subprocess.check_output(command).strip()
    elif platform.system() == "Linux":
      command = "cat /proc/cpuinfo"
      all_info = subprocess.check_output(command, shell=True).decode().strip()
      for line in all_info.split("\n"):
        if "model name" in line:
          return re.sub( ".*model name.*:", "", line,1)
    return ""

if __name__ == '__main__':
  print(get_processor_platform())  
  
  l = Logger('PROC', base_folder='.', app_folder='_cache')