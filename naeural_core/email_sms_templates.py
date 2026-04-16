"""Shared notification templates for email and SMS payload staging.

The core package keeps a minimal, pipeline-only wording contract so the PyPI
artifact remains neutral across deployments. Edge runtimes may enrich the
operator-facing text by defining `extensions.utils.email_sms_templates` with
the same constant names.
"""

# Keep the core package neutral: the default context should only expose the
# pipeline name. Edge deployments can override this from `extensions/`.
NOTIFICATION_CONTEXT_TEMPLATE = "{stream_name}"
NOTIFICATION_EMAIL_SUBJECT_TEMPLATE = "{context}"
NOTIFICATION_TRANSITION_PREFIX_TEMPLATE = "{context}"
NOTIFICATION_SINGLE_ALERTER_TEMPLATE = "alert was {raised_or_lowered}"
NOTIFICATION_MULTI_ALERTER_TEMPLATE = (
  "{alerter_count} alerts were {raised_or_lowered}: {alerters}"
)

try:
  # Match the existing audited-extension pattern used elsewhere in the runtime.
  # The override is optional and intentionally process-wide because the user
  # asked for one shared notification wording contract across all plugins.
  from extensions.utils.email_sms_templates import (
    NOTIFICATION_CONTEXT_TEMPLATE,
    NOTIFICATION_EMAIL_SUBJECT_TEMPLATE,
    NOTIFICATION_TRANSITION_PREFIX_TEMPLATE,
    NOTIFICATION_SINGLE_ALERTER_TEMPLATE,
    NOTIFICATION_MULTI_ALERTER_TEMPLATE,
  )
except ImportError:
  # The default package behavior is valid on its own; deployments that do not
  # ship an audited E2 override simply keep the minimal core wording.
  pass
