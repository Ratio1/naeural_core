from naeural_core import constants as ct

class _EmailerMixin(object):
  """Expose flat notification configuration getters for mail and SMS.

  Notes
  -----
  The mixin intentionally keeps the configuration surface narrow: the runtime
  reads already-structured dictionaries from the instance config and leaves all
  transport-specific parsing to the upstream configuration layer.
  """
  def __init__(self):
    super(_EmailerMixin, self).__init__()
    return

  @staticmethod
  def _get_enabled_notification_config(config):
    """Return a channel config only when it carries a concrete provider slug.

    Parameters
    ----------
    config : Any
      Raw channel configuration stored in ``_instance_config``.

    Returns
    -------
    dict or None
      The original configuration dictionary when it is already structured and
      its ``PROVIDER`` field names an actual delivery provider. Blank strings
      and serialized null-like sentinels such as ``"none"``, ``"null"``, and
      ``"undefined"`` behave like a disabled channel and therefore normalize
      to ``None``.
    """
    if not isinstance(config, dict):
      return None

    # Normalizing to lowercase lets the runtime treat UI-serialized null-ish
    # values as disabled channels regardless of casing while keeping any real
    # provider slug, such as "resend" or "web2sms", intact.
    provider = str(config.get("PROVIDER", "") or "").strip().lower()
    if provider in ("", "none", "null", "undefined"):
      return None
    return config

  @property
  def cfg_email_config(self):
    """Return the flat email configuration dictionary when present.

    Returns
    -------
    dict or None
      The structured ``EMAIL_CONFIG`` dictionary when the instance config
      already stores one, otherwise ``None``.
    """
    config = self._instance_config.get(ct.EMAIL_NOTIFICATION.EMAIL_CONFIG, None)
    return self._get_enabled_notification_config(config)

  @property
  def cfg_sms_config(self):
    """Return the flat SMS configuration dictionary when present.

    Returns
    -------
    dict or None
      The structured ``SMS_CONFIG`` dictionary when the instance config
      already stores one, otherwise ``None``.
    """
    config = self._instance_config.get(ct.SMS_NOTIFICATION.SMS_CONFIG, None)
    return self._get_enabled_notification_config(config)
