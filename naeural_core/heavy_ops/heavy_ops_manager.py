"""Heavy-operation startup manager.

This module owns the runtime merge contract for startup ``HEAVY_OPS_CONFIG``.
Deployments can add asynchronous or communication-thread operations, but the
notification dispatchers are treated as safe runtime defaults:

.. code-block:: json

  {
    "HEAVY_OPS_CONFIG": {
      "DISABLE_DEFAULT_SEND_MAIL": false,
      "DISABLE_DEFAULT_SEND_SMS": false,
      "ACTIVE_COMM_ASYNC": [
        "send_mail",
        "send_sms",
        "save_image_dataset"
      ],
      "ACTIVE_ON_COMM_THREAD": []
    }
  }

``send_mail`` and ``send_sms`` stay enabled even when
``ACTIVE_COMM_ASYNC`` is overridden with custom operations. Operators must use
``DISABLE_DEFAULT_SEND_MAIL`` or ``DISABLE_DEFAULT_SEND_SMS`` to opt out of
runtime auto-injection. If an operator explicitly lists a disabled operation in
``ACTIVE_COMM_ASYNC``, the explicit list wins; the disable flag only prevents
default insertion.
"""

from naeural_core.manager import Manager
from naeural_core import constants as ct


DEFAULT_COMM_ASYNC_NOTIFICATION_OPS = (
  (ct.HEAVY_OPS.SEND_MAIL, ct.HEAVY_OPS.DISABLE_DEFAULT_SEND_MAIL),
  (ct.HEAVY_OPS.SEND_SMS, ct.HEAVY_OPS.DISABLE_DEFAULT_SEND_SMS),
)

DEFAULT_HEAVY_OPS_CONFIG = {
  ct.HEAVY_OPS.DISABLE_DEFAULT_SEND_MAIL : False,
  ct.HEAVY_OPS.DISABLE_DEFAULT_SEND_SMS : False,

  ct.HEAVY_OPS.ACTIVE_COMM_ASYNC : [
    ct.HEAVY_OPS.SEND_MAIL,
    ct.HEAVY_OPS.SEND_SMS,
    "save_image_dataset",
  ],

  ct.HEAVY_OPS.ACTIVE_ON_COMM_THREAD : [
  ]     
}


def _as_list(value):
  """Return ``value`` as a list while preserving list-like config values.

  Parameters
  ----------
  value : Any
    Raw config value read from the startup configuration.

  Returns
  -------
  list
    Empty list for ``None``, a shallow list copy for list/tuple inputs, or a
    single-item list for scalar values. The scalar fallback keeps startup
    normalization defensive without hiding malformed values later in plugin
    loading.
  """
  if value is None:
    return []
  if isinstance(value, (list, tuple)):
    return list(value)
  return [value]


def _is_truthy_config_value(value):
  """Return whether a startup-config value explicitly enables a boolean flag.

  Parameters
  ----------
  value : Any
    Raw value from JSON or env-expanded startup config.

  Returns
  -------
  bool
    ``True`` for boolean true, non-zero numbers, and common textual true
    spellings. Empty strings and false-like strings return ``False``.
  """
  if isinstance(value, str):
    return value.strip().lower() in ("1", "true", "yes", "y", "on")
  return bool(value)


def _dedupe_preserving_order(values):
  """Remove duplicate operation names while preserving first occurrence order.

  Parameters
  ----------
  values : list
    Operation names assembled from default notification operations and the
    configured custom operation list.

  Returns
  -------
  list
    De-duplicated operation names in the order they should be initialized.
  """
  result = []
  seen = set()
  for value in values:
    if value in seen:
      continue
    seen.add(value)
    result.append(value)
  return result


def resolve_heavy_ops_config(config):
  """Merge startup heavy-op config with default notification dispatchers.

  Parameters
  ----------
  config : dict or None
    Raw ``HEAVY_OPS_CONFIG`` value from startup config. A deployment may
    provide custom async operations, but mail and SMS remain default-enabled
    unless ``DISABLE_DEFAULT_SEND_MAIL`` or ``DISABLE_DEFAULT_SEND_SMS`` is set.

  Returns
  -------
  dict
    Normalized heavy-op configuration with ``ACTIVE_COMM_ASYNC`` containing
    default notification dispatchers plus any configured custom operations.

  Notes
  -----
  The merge is intentionally performed inside the runtime instead of relying
  on every deployment config to repeat notification operations. This prevents
  an unrelated override such as ``["save_image_dataset"]`` from silently
  disabling alert email or SMS delivery.
  """
  config = {} if config is None else dict(config)
  result = {
    key: _as_list(value) if key in (
      ct.HEAVY_OPS.ACTIVE_COMM_ASYNC,
      ct.HEAVY_OPS.ACTIVE_ON_COMM_THREAD,
    ) else value
    for key, value in DEFAULT_HEAVY_OPS_CONFIG.items()
  }

  for key, value in config.items():
    if key in (ct.HEAVY_OPS.ACTIVE_COMM_ASYNC, ct.HEAVY_OPS.ACTIVE_ON_COMM_THREAD):
      result[key] = _as_list(value)
    else:
      result[key] = value

  has_configured_async_ops = ct.HEAVY_OPS.ACTIVE_COMM_ASYNC in config
  active_comm_async = list(result[ct.HEAVY_OPS.ACTIVE_COMM_ASYNC])
  default_comm_async = []
  for operation_name, disable_key in DEFAULT_COMM_ASYNC_NOTIFICATION_OPS:
    if _is_truthy_config_value(result.get(disable_key, False)) and not has_configured_async_ops:
      active_comm_async = [value for value in active_comm_async if value != operation_name]
      continue

    # The flag disables only runtime auto-injection of the default operation.
    # Operators can still list the operation explicitly in ACTIVE_COMM_ASYNC if
    # they need a transitional config that documents the final active list.
    if not _is_truthy_config_value(result.get(disable_key, False)) and operation_name not in active_comm_async:
      default_comm_async.append(operation_name)

  result[ct.HEAVY_OPS.ACTIVE_COMM_ASYNC] = _dedupe_preserving_order(default_comm_async + active_comm_async)
  return result


class HeavyOpsManager(Manager):

  def __init__(self, log, shmem, **kwargs):
    self.shmem = shmem
    self._dct_ops = None
    super(HeavyOpsManager, self).__init__(log=log, prefix_log='[HOPSM]', **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_data = resolve_heavy_ops_config(
      self.config_data.get(ct.HEAVY_OPS.HEAVY_OPS_CONFIG, DEFAULT_HEAVY_OPS_CONFIG)
    )
    self._dct_ops = self._dct_subalterns
    # 1st category as heavy ops that run on separate individual threads without (usually)
    # affecting inplace the payload
    for operation_name in self.config_data.get(ct.HEAVY_OPS.ACTIVE_COMM_ASYNC, []):
      self.create_heavy_operation(operation_name, comm_async=True)
      
    # 2nd category are heavy ops that do not use separate thread and run on comms thread
    # this second category is usually for plugins that change inplace the payload
    for operation_name in self.config_data.get(ct.HEAVY_OPS.ACTIVE_ON_COMM_THREAD, []):
      self.create_heavy_operation(operation_name, comm_async=False)
    return

  def _get_plugin_class(self, name):
    _module_name, _class_name, _class_def, _class_config = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_HEAVY_OPS_PLUGINS,
      name=name,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_HEAVY_OPS_PLUGINS,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_HEAVY_OPS_PLUGINS,
      safety_check=True, # perform safety check           
    )

    if _class_def is None:
      msg = "Error loading heavy_ops plugin '{}'".format(name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info="No code/script defined for heavy_ops plugin '{}' in {}".format(name, ct.PLUGIN_SEARCH.LOC_HEAVY_OPS_PLUGINS)
      )
    #endif

    return _class_def, _class_config

  def create_heavy_operation(self, name, comm_async):
    _cls, _config = self._get_plugin_class(name)

    try:
      op = _cls(log=self.log, shmem=self.shmem, config=_config, comm_async=comm_async)
      self._dct_ops[name] = op
    except Exception as exc:
      msg = "Exception '{}' when initializing heavy_ops plugin {}".format(exc, name)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
    #end try-except
    return

  def run_all_comm_async(self, msg):
    for name, op in self._dct_ops.items():
      if not op.comm_async:
        continue
      op.process_payload(msg)

    return

  def run_all_on_comm_thread(self, msg):
    for name, op in self._dct_ops.items():
      if op.comm_async:
        continue
      op.process_payload(msg)

    return
