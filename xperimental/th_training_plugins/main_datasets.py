from naeural_core.core_logging import SBLogger
from naeural_core.local_libraries.nn.th.training.data.simple_image_classifier import SimpleImageClassifierDataLoaderFactory

if __name__ == '__main__':

  log = SBLogger()
  dev_data_factory = SimpleImageClassifierDataLoaderFactory(
    log=log,
    num_workers=0,
    batch_size=16,
    path_to_dataset='./_local_cache/_data/POSE/2_dev',
    data_subset_name='dev',
    image_height=200,
    image_width=100,
    device='cpu',
    files_extensions=None # default vision behavior (.jpg, .png etc)
  )

  dev_data_factory.create()



