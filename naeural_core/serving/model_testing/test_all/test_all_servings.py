# global dependencies
import numpy as np
import os
import pandas as pd

# local dependencies
from PyE2 import load_dotenv
from naeural_core import constants as ct
from naeural_core import Logger
from naeural_core.serving.model_testing.base import Base
from naeural_core.serving.model_testing.test_all.data import files_dict as TEST_FILES


SORT_BY_RAW = True


EXCEPTION_FILES = [
  '__init__.py',
]


EXCEPTION_SERVINGS = [
  # 'th_cqc',
  'th_iqa_fast', 'th_iqa_slow',
  'th_yf_xp', 'th_rface_id',
  'th_yf_base', 'code_llama_v2',
  'th_effdet7', 'th_plano_s1',
  'th_yf5l', 'th_yf5s',
]


class GenericServingTester(Base):
  def draw_inference_particular_template(self, img, inference, **kwargs):
    return img

  def draw_inference_default(self, img, inference, **kwargs):
    # First, we get the label
    label = kwargs.get('label', None)
    if label is None:
      inf_type = inference.get(ct.TYPE, None) or ''
      inf_prc = inference.get(ct.PROB_PRC, None) or 0
      label = f'{inf_type} | {round(float(inf_prc), 2)}'
    # endif label is None
    # Then, we draw the inference
    top, left, bottom, right = inference[ct.TLBR_POS]
    color = kwargs.get('color', ct.GREEN)
    img = self._painter.draw_detection_box(
      image=img,
      top=top,
      left=left,
      bottom=bottom,
      right=right,
      label=label,
      prc=round(float(inference[ct.PROB_PRC]), 2),
      color=color,
      font_scale=1
    )
    return img

  def draw_inference(self, img, inference, **kwargs):
    if self.get_model_name() in []:
      return self.draw_inference_particular_template(img, inference, **kwargs)
    return self.draw_inference_default(img, inference, **kwargs)

  def plot(self, dataset_name, **kwargs):
    lst_imgs = self.get_images(dataset_name)
    preds = self.get_last_preds()
    for i in range(len(lst_imgs)):
      img = lst_imgs[i][:, :, ::-1]
      img = np.ascontiguousarray(img)
      curr_preds = preds['INFERENCES'][0][i]

      for pred in curr_preds:
        img = self.draw_inference(
          img=img,
          inference=pred,
          is_valid=True,
          color=ct.GREEN
        )

      if self._show_plots:
        self._painter.show(self._model_name, img)
      if self._save_plots:
        self._painter.save(
          img,
          fn='{}/{}/{}.jpg'.format(
            self._testing_subfolder_path,
            dataset_name,
            f'{i + 1}'
          )
        )
      # endif self._save_plots
    # endfor
    return


def compress_dict(nested_dict, key):
  res = []
  for id, lst_files in nested_dict.items():
    for fn in lst_files:
      res.append(fn)
    # endofr lst_files
  # endfor nested_dict.items()
  return {
    key: res
  }


if __name__ == '__main__':
  import multiprocessing as mp
  mp.set_start_method('spawn')
  log = Logger('MTA', base_folder='.', app_folder='_local_cache', TF_KERAS=False)
  GPU_DEVICE = 0

  # check if the serving files exist
  servings_dir = 'core/serving/default_inference'
  if not os.path.exists(servings_dir):
    log.P(f'ERROR: {servings_dir} does not exist')
    log.P(f'Exiting...')
    exit(1)
  # endif not os.path.exists

  # get the serving files
  servings = os.listdir(servings_dir)
  # retrieve the serving names
  serving_names = [
    s[:-3] for s in servings if s.endswith('.py') and s not in EXCEPTION_FILES
  ]
  # define the used data and the tests for each serving
  serving_data = {
    k: {
      'data': TEST_FILES,
      'tests': [{}]  # this will run the serving with its default configuration
    }
    for k in serving_names if k not in EXCEPTION_SERVINGS
  }

  total_df = pd.DataFrame()
  save_subdir = os.path.join('testing', f'{log.file_prefix}_TEST_ALL')

  for i, (serving_name, test_dict) in enumerate(serving_data.items()):
    current_tests = test_dict['tests']
    current_data = test_dict['data']
    log.P(f'[({i + 1} / {len(serving_data)})]Running tests for serving {serving_name} with test configs:\n{current_tests}')
    try:
      test_process = GenericServingTester(
        log=log,
        model_name=serving_name,
        test_files=current_data,
        gpu_device=GPU_DEVICE,
        save_plots=False,
        show_plots=False,
        nr_warmup=0,
        nr_predicts=1,
        inprocess=False,
        print_errors=True,
        label_extension='txt'
      )

      load_dotenv()
      dct_params = {
        "WITNESS_INFO": True,
        "NEW_PARAM": 0,
        'USE_FP16': False,
        'MAX_WAIT_TIME': 20,
        'DEFAULT_DEVICE': 'cuda:' + str(GPU_DEVICE),
        "MODEL_ZOO_CONFIG": {
          "endpoint": os.environ["EE_MINIO_ENDPOINT"],
          "access_key": os.environ["EE_MINIO_ACCESS_KEY"],
          "secret_key": os.environ["EE_MINIO_SECRET_KEY"],
          "secure": os.environ["EE_MINIO_SECURE"],
          "bucket_name": "model-zoo"
        }
      }
      current_df = test_process.run_tests(
        lst_tests=current_tests,
        dct_params=dct_params
      )
      total_df = pd.concat([total_df, current_df])
      log.save_dataframe(total_df, 'results.csv', folder='output', subfolder_path=save_subdir)
      log.P(f'[({i + 1} / {len(serving_data)})]Successfully done tests for serving {serving_name}')
    except Exception as e:
      log.P(f'[({i + 1} / {len(serving_data)})]Failed tests for serving {serving_name} with test configs:\n{current_tests}')
  # endfor serving_data

