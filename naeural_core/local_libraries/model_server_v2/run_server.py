

import os
import sys
sys.path.append(os.getcwd())

import argparse
import json

from naeural_core import Logger
from naeural_core.local_libraries.model_server_v2 import FlaskModelServer

if __name__ == '__main__':
  parser = argparse.ArgumentParser()

  parser.add_argument(
    '-b', '--base_folder',
    type=str, default='libraries',
    help='Logger base folder'
  )

  parser.add_argument(
    '-a', '--app_folder',
    type=str, default='_logger_cache',
    help='Logger app folder'
  )

  parser.add_argument(
    '--config_endpoint', type=json.loads,
    help='JSON configuration of the endpoint'
  )

  parser.add_argument(
    '--host', type=str, default='127.0.0.1'
  )

  parser.add_argument(
    '--port', type=int, default=5002
  )

  parser.add_argument(
    '--execution_path', type=str, default='/analyze'
  )

  parser.add_argument(
    '--workers_location', type=str
  )

  parser.add_argument(
    '--worker_name', type=str
  )


  parser.add_argument(
    '--microservice_name', type=str
  )

  parser.add_argument(
    '--microservice_code', type=str
  )

  parser.add_argument(
    '--worker_suffix', type=str, default='Worker'
  )

  parser.add_argument(
    '--nr_workers', type=int
  )

  parser.add_argument(
    '--use_tf', action='store_true'
  )

  args = parser.parse_args()
  base_folder = args.base_folder
  app_folder = args.app_folder
  config_endpoint = args.config_endpoint
  host = args.host
  port = args.port
  execution_path = args.execution_path
  workers_location = args.workers_location
  worker_name = args.worker_name
  worker_suffix = args.worker_suffix
  microservice_name = args.microservice_name
  microservice_code = args.microservice_code
  nr_workers = args.nr_workers
  use_tf = args.use_tf
  
  if microservice_code is None:
    microservice_code = Logger.name_abbreviation(microservice_name)

  log = Logger(
    lib_name=microservice_code,
    base_folder=base_folder,
    app_folder=app_folder,
    TF_KERAS=False, # use_tf
    max_lines=3000
  )

  svr = FlaskModelServer(
    log=log,
    workers_location=workers_location,
    worker_name=worker_name,
    microservice_name=microservice_name,
    worker_suffix=worker_suffix,
    host=host,
    port=port,
    config_endpoint=config_endpoint,
    execution_path=execution_path,
    nr_workers=nr_workers
  )
