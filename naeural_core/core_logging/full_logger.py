from functools import partial

from naeural_client import Logger as BaseLogger
from .logger_mixins import (
  _AdvancedTFKerasMixin,
  _BasicPyTorchMixin,
  _BasicTFKerasMixin,
  _BetaInferenceMixin,
  _ComplexNumpyOperationsMixin,
  _ConfusionMatrixMixin,
  _DataFrameMixin,
  _DeployModelsInProductionMixin,
  _FitDebugTFKerasMixin,
  _GPUMixin,
  _GridSearchMixin,
  _HistogramMixin,
  _KerasCallbacksMixin,
  _MachineMixin,
  _MatplotlibMixin,
  _MultithreadingMixin,
  _NLPMixin,
  _PackageLoaderMixin,
  _PublicTFKerasMixin,
  _TF2ModulesMixin,
  _TimeseriesBenchmakerMixin,
  _VectorSpaceMixin,
  _LlmUtilsMixin
)


class Logger(
  BaseLogger,
  _MatplotlibMixin,
  _ConfusionMatrixMixin,
  _HistogramMixin,
  _ComplexNumpyOperationsMixin,
  _DataFrameMixin,
  _NLPMixin,
  _GridSearchMixin,
  _TimeseriesBenchmakerMixin,
  _VectorSpaceMixin,
  _LlmUtilsMixin,
  _MachineMixin,
  _GPUMixin,
  _PackageLoaderMixin,
  _PublicTFKerasMixin,
  _BasicTFKerasMixin,
  _AdvancedTFKerasMixin,
  _DeployModelsInProductionMixin,
  _FitDebugTFKerasMixin,
  _KerasCallbacksMixin,
  _TF2ModulesMixin,
  _BasicPyTorchMixin,
  _BetaInferenceMixin,
  _MultithreadingMixin,
):

  def __init__(self, lib_name="",
               lib_ver="",
               config_file="",
               base_folder=None,
               app_folder=None,
               config_data={},
               show_time=True,
               config_file_encoding=None,
               no_folders_no_save=False,
               max_lines=None,
               HTML=False,
               DEBUG=True,
               data_config_subfolder=None,
               check_additional_configs=False,
               TF_KERAS=False,
               BENCHMARKER=False,
               dct_bp_params=None,
               default_color='n',
               ):

    super(Logger, self).__init__(
      lib_name=lib_name, lib_ver=lib_ver,
      config_file=config_file,
      base_folder=base_folder,
      app_folder=app_folder,
      show_time=show_time,
      config_data=config_data,
      config_file_encoding=config_file_encoding,
      no_folders_no_save=no_folders_no_save,
      max_lines=max_lines,
      HTML=HTML,
      DEBUG=DEBUG,
      data_config_subfolder=data_config_subfolder,
      check_additional_configs=check_additional_configs,
      default_color=default_color,
    )

    how_runs = ''
    if self.runs_from_ipython():
      how_runs = ' running in ipython'
    if self.runs_with_debugger():
      how_runs = ' running in debug mode'
    self.verbose_log(
        "  Python {}{}, Git branch: '{}', Conda: '{}'".format(
          self.python_version,
          how_runs,
          self.git_branch,
          self.conda_env,
        ), color='green'
      )

    self.verbose_log("  Os: {}".format(self.get_os_name()), color='g')
    self.verbose_log("  IP: {}".format(self.get_localhost_ip()), color='g')

    self.verbose_log('  Avail/Total RAM: {:.1f} GB / {:.1f} GB'.format(
      self.get_avail_memory(), self.get_machine_memory()
    ), color='green')

    if BENCHMARKER:
      dct_bp_params = dct_bp_params or {}
      self._setup_benchmarker(**dct_bp_params)

    if TF_KERAS:
      self.check_tf()

    return


SBLogger = partial(Logger, lib_name='tst', base_folder='.', app_folder='_local_cache')

if __name__ == '__main__':
  l = Logger('TEST', base_folder='Dropbox', app_folder='_libraries_testdata')
  l.P("All check", color='green')
