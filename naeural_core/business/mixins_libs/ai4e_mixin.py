AI4E_MIXIN_CONFIG = {
  'REPORT_PERIOD': 10*60,
  'OBJECTIVE_NAME': None,
  'OBJECT_TYPE': ['person'],
  'CLASSES': None,

  "DESCRIPTION": "",
  "REWARDS": {},
  "DATASET": {},
  "CREATION_DATE": None,
  "DATA_SOURCES": [],

  "VALIDATION_RULES": {
  },
}


class _Ai4eMixin(object):
  def get_status_payload_additional_kwargs(self, **kwargs):
    return {}

  def get_dataset_details(self):
    return {**self.cfg_dataset}

  def get_ds_classes(self):
    classes = self.cfg_classes
    if classes is None:
      classes = self.cfg_object_type
    if not isinstance(classes, (list, dict)):
      classes = [classes]
    if not isinstance(classes, dict):
      classes = {k: k for k in classes}
      return classes
    return classes

  def get_job_status(self):
    return 'Not implemented'

  def get_status_payload_kwargs(self, **kwargs):
    res = {
      'is_status': True,
      "objective_name": self.cfg_objective_name,
      "rewards": self.cfg_rewards,
      "dataset": self.get_dataset_details(),
      "creation_date": self.cfg_creation_date,
      "data_sources": self.cfg_data_sources,
      "target": self.cfg_object_type,
      "classes": self.get_ds_classes(),
      "description": self.cfg_description,
      "job_status": self.get_job_status(),
      **self.get_status_payload_additional_kwargs(**kwargs),
    }

    return res

  def maybe_report_status(self, force=False, **kwargs):
    """
    Periodically report the status to the AI4E plugins.
    """
    if force or self.last_payload_time is None or self.time() - self.last_payload_time > self.cfg_report_period:
      status_payload_kwargs = self.get_status_payload_kwargs()
      self.add_payload_by_fields(**status_payload_kwargs)
    # endif report time
    return

