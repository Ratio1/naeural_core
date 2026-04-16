"""Standalone regressions for notification payload metadata staging.

This module exercises the narrow contract between `GeneralPayload` and the
notification heavy ops. The checks are intentionally small and rely on a fake
owner surface so the regression focuses on metadata emission rather than the
rest of the plugin runtime.
"""

import importlib.util
from pathlib import Path
import sys
import unittest

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(_PACKAGE_ROOT) not in sys.path:
  sys.path.insert(0, str(_PACKAGE_ROOT))

from naeural_core import constants as ct
from naeural_core import data_structures as data_structures_module
from naeural_core.data_structures import GeneralPayload

# Load the mixin directly from its module file so the regression does not pull
# in optional package-level dependencies from `business.mixins_base.__init__`.
_EMAILER_MIXIN_SPEC = importlib.util.spec_from_file_location(
  "test_notification_payload_emailer_mixin",
  _PACKAGE_ROOT / "naeural_core" / "business" / "mixins_base" / "emailer_mixin.py",
)
_EMAILER_MIXIN_MODULE = importlib.util.module_from_spec(_EMAILER_MIXIN_SPEC)
_EMAILER_MIXIN_SPEC.loader.exec_module(_EMAILER_MIXIN_MODULE)
_EmailerMixin = _EMAILER_MIXIN_MODULE._EmailerMixin

_DEFAULT_CONFIG = object()


def _normalize_notification_config(config):
  """Mirror the runtime rule that blank or null-like providers disable a channel.

  Parameters
  ----------
  config : dict or None or object
      Notification config value injected into the fake owner.

  Returns
  -------
  dict or None
      The original config when it carries a concrete provider slug, otherwise
      ``None`` so the fake owner matches the live getter semantics.
  """
  if not isinstance(config, dict):
    return None
  provider = str(config.get("PROVIDER", "") or "").strip().lower()
  if provider in ("", "none", "null", "undefined"):
    return None
  return config


class _FakeNotificationOwner:
  """Minimal owner stub for `GeneralPayload._add_metadata_to_payload()`.

  The stub exposes only the properties and methods required by the payload
  staging logic. That keeps the regressions close to the real contract while
  avoiding any dependency on the wider plugin runtime or the alert helper
  implementation.
  """

  def __init__(self, changed, email_config=_DEFAULT_CONFIG, sms_config=_DEFAULT_CONFIG, metadata=None):
    """Initialize the stub with a fixed alert transition state.

    Parameters
    ----------
    changed : bool
      Whether the alert state should behave as if it has changed for the
      current payload build.

    email_config : dict or None, optional
      Email configuration dictionary surfaced through ``cfg_email_config``.

    sms_config : dict or None, optional
      SMS configuration dictionary surfaced through ``cfg_sms_config``.

    metadata : dict or None, optional
      Capture metadata returned by `dataapi_all_metadata()`.
    """
    self._changed = changed
    self._stream_id = "stream-a"
    self._signature = "plugin-x"
    self._device_id = "device-1"
    self.cfg_instance_id = "instance-1"
    self.cfg_email_config = _normalize_notification_config(
      {"PROVIDER": "resend"} if email_config is _DEFAULT_CONFIG else email_config
    )
    self.cfg_sms_config = _normalize_notification_config(
      {"PROVIDER": "web2sms"} if sms_config is _DEFAULT_CONFIG else sms_config
    )
    self._metadata = {} if metadata is None else metadata
    self.alerters_names = ["default"]

  def dataapi_all_metadata(self):
    """Return no extra metadata for the payload contract tests.

    Returns
    -------
    dict
      Always returns an empty dictionary so the regression only exercises the
      notification-specific payload fields.
    """
    return self._metadata

  def get_stream_id(self):
    """Return the stream identifier used in staged notification text.

    Returns
    -------
    str
      Fixed stream identifier used by the notification summary assertions.
    """
    return self._stream_id

  def alerter_status_changed(self, alerter="default"):
    """Report whether the fake alert state should be treated as changed.

    Parameters
    ----------
    alerter : str, optional
      Alerter name accepted for interface compatibility with the production
      owner surface.

    Returns
    -------
    bool
      ``True`` when the stub should behave as if the alert transitioned.
    """
    _ = alerter
    return self._changed

  def alerter_is_new_raise(self, alerter="default"):
    """Report a raise transition when the regression asks for one.

    Parameters
    ----------
    alerter : str, optional
      Alerter name accepted for interface compatibility with the production
      owner surface.

    Returns
    -------
    bool
      Mirrors the configured changed state so the positive-path payload can
      stage a raise notification.
    """
    _ = alerter
    return self._changed

  def alerter_is_new_lower(self, alerter="default"):
    """Report no lower transition for the regression owner stub.

    Parameters
    ----------
    alerter : str, optional
      Alerter name accepted for interface compatibility with the production
      owner surface.

    Returns
    -------
    bool
      Always returns ``False`` because the regression only models a raise
      transition.
    """
    _ = alerter
    return False


class _FakeEmailerPlugin(_EmailerMixin):
  """Minimal plugin harness used to exercise the flat config getters.

  Notes
  -----
  The harness keeps the surface area intentionally small: only
  ``_instance_config`` is required for the mixin properties under test.
  """

  def __init__(self, instance_config):
    """Store the instance config consumed by `_EmailerMixin`.

    Parameters
    ----------
    instance_config : dict
      Instance configuration dictionary exposed to the mixin properties.
    """
    self._instance_config = instance_config
    super().__init__()


def _build_payload(changed, email_config=_DEFAULT_CONFIG, sms_config=_DEFAULT_CONFIG, metadata=None):
  """Create a bare `GeneralPayload` instance backed by the fake owner.

  Parameters
  ----------
  changed : bool
      Whether the fake owner should emulate a status transition.

  email_config : dict or None, optional
      Email configuration injected into the fake owner.

  sms_config : dict or None, optional
      SMS configuration injected into the fake owner.

  metadata : dict or None, optional
      Capture metadata returned by the fake owner.

  Returns
  -------
  GeneralPayload
      Payload instance with the fake owner attached and no extra fields.
  """
  payload = object.__new__(GeneralPayload)
  payload.owner = _FakeNotificationOwner(
    changed=changed,
    email_config=email_config,
    sms_config=sms_config,
    metadata=metadata,
  )
  return payload


def test_emailer_mixin_accepts_only_dict_configs():
  """Verify the mixin exposes only enabled flat dict configs.

  The regression locks down the new config boundary by asserting that already
  structured dicts are returned intact while non-dict values, blank-provider
  configs, and null-like provider sentinels resolve to ``None``.
  """
  plugin = _FakeEmailerPlugin({
    ct.EMAIL_NOTIFICATION.EMAIL_CONFIG: {"PROVIDER": "resend"},
    ct.SMS_NOTIFICATION.SMS_CONFIG: {"PROVIDER": "web2sms"},
  })
  assert plugin.cfg_email_config == {"PROVIDER": "resend"}
  assert plugin.cfg_sms_config == {"PROVIDER": "web2sms"}

  legacy_plugin = _FakeEmailerPlugin({
    ct.EMAIL_NOTIFICATION.EMAIL_CONFIG: "legacy:smtp:string",
    ct.SMS_NOTIFICATION.SMS_CONFIG: ["web2sms"],
  })
  assert legacy_plugin.cfg_email_config is None
  assert legacy_plugin.cfg_sms_config is None

  blank_provider_plugin = _FakeEmailerPlugin({
    ct.EMAIL_NOTIFICATION.EMAIL_CONFIG: {"PROVIDER": "   ", "TO": []},
    ct.SMS_NOTIFICATION.SMS_CONFIG: {"PROVIDER": "", "TO": []},
  })
  assert blank_provider_plugin.cfg_email_config is None
  assert blank_provider_plugin.cfg_sms_config is None

  null_like_provider_plugin = _FakeEmailerPlugin({
    ct.EMAIL_NOTIFICATION.EMAIL_CONFIG: {"PROVIDER": "Undefined", "TO": []},
    ct.SMS_NOTIFICATION.SMS_CONFIG: {"PROVIDER": "NONE", "TO": []},
  })
  assert null_like_provider_plugin.cfg_email_config is None
  assert null_like_provider_plugin.cfg_sms_config is None


def test_payload_preserves_generic_metadata_copy_before_notification_staging():
  """Verify generic metadata copy still runs before notification staging.

  The refactor must not break the existing `_C_*` metadata copy rules or the
  direct-key exceptions. This regression keeps both behaviors under test while
  also confirming that no notification fields are emitted when the alerter did
  not change.
  """
  payload = _build_payload(
    changed=False,
    metadata={
      "camera_id": "cam-01",
      "payload_context": {"zone": "gate-a"},
      "temp_data": "skip-me",
    },
  )

  payload._add_metadata_to_payload()

  assert vars(payload)["_C_camera_id"] == "cam-01"
  assert vars(payload)["PAYLOAD_CONTEXT"] == {"zone": "gate-a"}
  assert "_C_temp_data" not in vars(payload)
  assert ct.SEND_EMAIL not in vars(payload)
  assert ct.SEND_SMS not in vars(payload)


def test_payload_only_stages_channel_flags_on_alert_change():
  """Verify notification metadata stays absent until an alert changes.

  The regression protects the payload contract so a steady-state plugin does
  not emit notification control fields merely because email and SMS configs
  are available on the owner.
  """
  payload = _build_payload(changed=False)

  payload._add_metadata_to_payload()

  assert "_H_EMAIL_CONFIG" not in vars(payload)
  assert "_H_SMS_CONFIG" not in vars(payload)
  assert ct.SEND_EMAIL not in vars(payload)
  assert ct.SEND_SMS not in vars(payload)


def test_payload_skips_blank_provider_notification_dicts():
  """Verify blank-provider dict configs behave like disabled channels.

  The plugin-level contract now exposes explicit dict templates for readers, so
  the staging logic must still treat a blank provider as an inactive channel.
  """
  payload = _build_payload(
    changed=True,
    email_config={"PROVIDER": "   ", "TO": []},
    sms_config={"PROVIDER": "", "TO": []},
  )

  payload._add_metadata_to_payload()

  assert "_H_EMAIL_CONFIG" not in vars(payload)
  assert "_H_SMS_CONFIG" not in vars(payload)
  assert ct.SEND_EMAIL not in vars(payload)
  assert ct.SEND_SMS not in vars(payload)


def test_payload_stages_both_email_and_sms_flags_when_alert_changes():
  """Verify alert transitions stage both notification channels together.

  The regression ensures the modern flat contract only emits notification
  metadata when an alert state actually changes, and that both email and SMS
  receive the same concise transition summary.
  """
  payload = _build_payload(changed=True)

  payload._add_metadata_to_payload()

  assert vars(payload)["_H_EMAIL_CONFIG"] == {"PROVIDER": "resend"}
  assert vars(payload)["_H_SMS_CONFIG"] == {"PROVIDER": "web2sms"}
  assert vars(payload)[ct.SEND_EMAIL] is True
  assert vars(payload)[ct.SEND_SMS] is True
  assert vars(payload)["_H_EMAIL_SUBJECT"] == "Automatic alert in EE 'device-1': stream-a:plugin-x:instance-1"
  assert vars(payload)["_H_EMAIL_MESSAGE"] == "On stream `stream-a`, alert was raised"
  assert vars(payload)["_H_SMS_MESSAGE"] == "On stream `stream-a`, alert was raised"


def test_payload_uses_notification_templates_for_subject_and_transition_text():
  """Verify notification text is assembled from module-level templates.

  The notification contract should keep the email subject and the shared
  transition summary configurable from one place at the top of the module.
  This regression patches those templates and confirms both heavy-op payloads
  pick up the customized strings.
  """
  original_subject_template = data_structures_module.NOTIFICATION_EMAIL_SUBJECT_TEMPLATE
  original_prefix_template = data_structures_module.NOTIFICATION_TRANSITION_PREFIX_TEMPLATE
  original_single_alerter_template = data_structures_module.NOTIFICATION_SINGLE_ALERTER_TEMPLATE

  try:
    data_structures_module.NOTIFICATION_EMAIL_SUBJECT_TEMPLATE = (
      "EE={device_id}|{stream_name}|{signature}|{instance_id}"
    )
    data_structures_module.NOTIFICATION_TRANSITION_PREFIX_TEMPLATE = "stream={stream_name}"
    data_structures_module.NOTIFICATION_SINGLE_ALERTER_TEMPLATE = "state={raised_or_lowered}"

    payload = _build_payload(changed=True)
    payload._add_metadata_to_payload()

    assert vars(payload)["_H_EMAIL_SUBJECT"] == "EE=device-1|stream-a|plugin-x|instance-1"
    assert vars(payload)["_H_EMAIL_MESSAGE"] == "stream=stream-a, state=raised"
    assert vars(payload)["_H_SMS_MESSAGE"] == "stream=stream-a, state=raised"
  finally:
    data_structures_module.NOTIFICATION_EMAIL_SUBJECT_TEMPLATE = original_subject_template
    data_structures_module.NOTIFICATION_TRANSITION_PREFIX_TEMPLATE = original_prefix_template
    data_structures_module.NOTIFICATION_SINGLE_ALERTER_TEMPLATE = original_single_alerter_template


def test_payload_stages_email_without_sms_config():
  """Verify email staging still works when only email config is present.

  This keeps the per-channel staging contract explicit so removing SMS config
  cannot accidentally suppress email notification metadata.
  """
  payload = _build_payload(changed=True, sms_config=None)

  payload._add_metadata_to_payload()

  assert vars(payload)["_H_EMAIL_CONFIG"] == {"PROVIDER": "resend"}
  assert vars(payload)[ct.SEND_EMAIL] is True
  assert "_H_SMS_CONFIG" not in vars(payload)
  assert ct.SEND_SMS not in vars(payload)


def test_payload_stages_sms_without_email_config():
  """Verify SMS staging still works when only SMS config is present.

  This mirrors the email-only regression so channel independence remains
  covered on both sides of the notification contract.
  """
  payload = _build_payload(changed=True, email_config=None)

  payload._add_metadata_to_payload()

  assert vars(payload)["_H_SMS_CONFIG"] == {"PROVIDER": "web2sms"}
  assert vars(payload)[ct.SEND_SMS] is True
  assert "_H_EMAIL_CONFIG" not in vars(payload)
  assert ct.SEND_EMAIL not in vars(payload)


def test_payload_keeps_sms_staging_when_email_flag_already_exists():
  """Verify an existing email send flag blocks only email staging.

  The regression protects the narrow contract for duplicate control fields:
  an already-staged email payload must not be overwritten, but SMS staging
  should still proceed normally when SMS config exists and the alert changed.
  """
  payload = _build_payload(changed=True)
  vars(payload)[ct.SEND_EMAIL] = False

  payload._add_metadata_to_payload()

  assert vars(payload)[ct.SEND_EMAIL] is False
  assert "_H_EMAIL_CONFIG" not in vars(payload)
  assert "_H_EMAIL_SUBJECT" not in vars(payload)
  assert "_H_EMAIL_MESSAGE" not in vars(payload)
  assert vars(payload)["_H_SMS_CONFIG"] == {"PROVIDER": "web2sms"}
  assert vars(payload)[ct.SEND_SMS] is True
  assert vars(payload)["_H_SMS_MESSAGE"] == "On stream `stream-a`, alert was raised"


def test_payload_keeps_email_staging_when_sms_flag_already_exists():
  """Verify an existing SMS send flag blocks only SMS staging.

  The regression mirrors the email-flag case so the contract stays symmetric:
  a preexisting SMS control field should not suppress email metadata staging
  when the alert transitions and email config exists.
  """
  payload = _build_payload(changed=True)
  vars(payload)[ct.SEND_SMS] = False

  payload._add_metadata_to_payload()

  assert vars(payload)[ct.SEND_SMS] is False
  assert vars(payload)["_H_EMAIL_CONFIG"] == {"PROVIDER": "resend"}
  assert vars(payload)[ct.SEND_EMAIL] is True
  assert vars(payload)["_H_EMAIL_SUBJECT"] == "Automatic alert in EE 'device-1': stream-a:plugin-x:instance-1"
  assert vars(payload)["_H_EMAIL_MESSAGE"] == "On stream `stream-a`, alert was raised"
  assert "_H_SMS_CONFIG" not in vars(payload)
  assert "_H_SMS_MESSAGE" not in vars(payload)


TEST_FUNCTIONS = (
  test_emailer_mixin_accepts_only_dict_configs,
  test_payload_preserves_generic_metadata_copy_before_notification_staging,
  test_payload_only_stages_channel_flags_on_alert_change,
  test_payload_skips_blank_provider_notification_dicts,
  test_payload_stages_both_email_and_sms_flags_when_alert_changes,
  test_payload_uses_notification_templates_for_subject_and_transition_text,
  test_payload_stages_email_without_sms_config,
  test_payload_stages_sms_without_email_config,
  test_payload_keeps_sms_staging_when_email_flag_already_exists,
  test_payload_keeps_email_staging_when_sms_flag_already_exists,
)


def load_tests(loader, tests, pattern):
  """Expose the regression functions to `unittest discover`.

  Parameters
  ----------
  loader : unittest.TestLoader
      Standard unittest loader provided by discovery.

  tests : unittest.TestSuite
      Existing suite assembled before the module hook runs.

  pattern : str
      Discovery filename pattern. It is accepted for compatibility only.

  Returns
  -------
  unittest.TestSuite
      Suite containing the standalone regression functions as test cases.
  """
  _ = loader, tests, pattern
  suite = unittest.TestSuite()
  suite.addTests([unittest.FunctionTestCase(test_func) for test_func in TEST_FUNCTIONS])
  return suite


def _run_all_tests():
  """Run the standalone regression checks when executed as a script.

  Returns
  -------
  None
      The helper raises immediately if any regression assertion fails.
  """
  for test_func in TEST_FUNCTIONS:
    test_func()


if __name__ == "__main__":
  _run_all_tests()
