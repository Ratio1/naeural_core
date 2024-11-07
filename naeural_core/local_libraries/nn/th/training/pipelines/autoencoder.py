import torch as th
from naeural_core.local_libraries.nn.th.training.pipelines.base import BaseTrainingPipeline

class AutoencoderTrainingPipeline(BaseTrainingPipeline):
  score_key = 'dev_loss'
  score_mode = 'min'
  model_loss = th.nn.BCELoss()
