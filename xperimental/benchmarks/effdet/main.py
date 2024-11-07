#global dependencies
import numpy as np
import pandas as pd

#local dependenciess
from naeural_core import Logger
from naeural_core.local_libraries.vision.benchmark.model_benchmarker import ModelBenchmarker
# from naeural_core.local_libraries.nn.tf.utils import tfodapi2_ckpt_to_graph

if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='EE_BMRK', 
    config_file=cfg_file, 
    max_lines=1000, 
    TF_KERAS=False
    )
  
  # np_imgs = np.random.randint(
  #   low=0,
  #   high=256,
  #   size=(18, 720, 1280, 3),
  #   dtype=np.uint8
  #   )
  
  # #ED2_None_BS
  # l = []
  # for nr in [1, 2, 4, 8]:
  #   mb = ModelBenchmarker(
  #     log=log,
  #     module_name='xperimental.benchmarks.effdet.effdet2_None_models',
  #     class_name='ED2_None_BS{}'.format(nr)
  #     )
  #   df = mb.run(
  #     inputs=np_imgs,
  #     batch_sizes=[nr]
  #     )
  #   l.append(df)
  # df = pd.concat(l)
  # df.reset_index(drop=True, inplace=True)
  # log.p('\n\n{}'.format(df))
  
  
  # #ED2_1132x640
  # l = []
  # for nr in [1, 2, 4, 8]:
  #   mb = ModelBenchmarker(
  #     log=log,
  #     module_name='xperimental.benchmarks.effdet.effdet2_1132x640_models',
  #     class_name='ED2_1132x640_BS{}'.format(nr)
  #     )
  #   df = mb.run(
  #     inputs=np_imgs,
  #     batch_sizes=[nr]
  #     )
  #   l.append(df)
  # df = pd.concat(l)
  # df.reset_index(drop=True, inplace=True)
  # log.p('\n\n{}'.format(df))
  
  
  # l = []
  # for nr in [1, 2, 4, 8]:
  #   mb = ModelBenchmarker(
  #     log=log,
  #     module_name='xperimental.benchmarks.effdet.effdet4_None_models',
  #     class_name='ED4_None_BS{}'.format(nr)
  #     )
  #   df = mb.run(
  #     inputs=np_imgs,
  #     batch_sizes=[nr]
  #     )
  #   l.append(df)
  # df = pd.concat(l)
  # df.reset_index(drop=True, inplace=True)
  # log.p('\n\n{}'.format(df))
  
  
  
  
  

  
  
  
  
  
  