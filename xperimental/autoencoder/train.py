from naeural_core.core_logging import SBLogger
from naeural_core.local_libraries.nn.th.training.pipelines.autoencoder import AutoencoderTrainingPipeline

if __name__ == '__main__':
  log = SBLogger()

  CONFIG = {
    'MODEL_NAME' : 'MNIST_DAE',
    'PRELOAD_DATA' : True,
    'DEVICE_LOAD_DATA' : 'cpu',
    'DEVICE_TRAINING' : 'cpu',
    'BATCH_SIZE' : 32,
    'EPOCHS' : 20,

  }

  pipeline = AutoencoderTrainingPipeline(
    log=log,
    signature='autoencoder',
    config=CONFIG,
    path_to_dataset='./_local_cache/_data/MNIST'
  )

  pipeline.run()

