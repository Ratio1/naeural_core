import multiprocessing as mp

from naeural_core import Logger

from naeural_core.serving.serving_manager import ServingManager

if __name__ == '__main__':
  mp.set_start_method('spawn')
  log = Logger('MPTF', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  MODEL_NAME =( 'th_y5s6s', 'default')
  NR_TESTS = 10

  # defining script constants
  SERVING_MANAGER = ServingManager(
    shmem={},
    log=log,
    prefix_log='[TFMGR]'
  )
  for i in range(1, NR_TESTS + 1):
    if i == 2:
      log.P("DEBUG!")
    SERVING_MANAGER.start_server(
      server_name=MODEL_NAME,
      inprocess=False,
      upstream_config={
        'USE_AMP': True, 'USE_FP16': False, 'GPU_PREPROCESS': True, 'DEFAULT_DEVICE': 'cuda:0',
       'MAX_BATCH_FIRST_STAGE': 1}
    )

    SERVING_MANAGER.stop_server(server_name=MODEL_NAME)
