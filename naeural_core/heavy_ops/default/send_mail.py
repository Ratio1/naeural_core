"""Heavy-op email delivery with provider-slug dispatch."""

import json

import requests

from naeural_core.heavy_ops.base import BaseHeavyOp
from naeural_core import constants as ct


CONFIG = {
  "IDLE_THREAD_SLEEP_TIME": 2,
}

_RESEND_EMAILS_URL = "https://api.resend.com/emails"


class SendMailHeavyOp(BaseHeavyOp):
  """Deliver notification emails from queued heavy-op payloads.

  The heavy-op keeps the registration boundary strict: `_register_payload_operation()`
  scrubs the live payload copy that continues through the pipeline while
  returning a queued copy that still carries the witness image data. The
  processing step then routes through a provider slug and sends the request
  through a provider-specific HTTP handler.

  Notes
  -----
  Only the `resend` provider is supported in this workspace. Unsupported
  provider tokens fail fast so the caller never falls back to an obsolete SMTP
  path.
  """

  def __init__(self, **kwargs):
    """Initialize the heavy op.

    Parameters
    ----------
    **kwargs : dict
      Keyword arguments forwarded to `BaseHeavyOp.__init__()`.
    """
    super(SendMailHeavyOp, self).__init__(**kwargs)
    return

  def startup(self):
    """Start the heavy op and assert asynchronous execution is enabled.

    Returns
    -------
    None
      The method performs startup side effects only.
    """
    super().startup()
    assert self.comm_async

  def _register_payload_operation(self, payload):
    """Scrub the live payload while returning the queued email work copy.

    Parameters
    ----------
    payload : dict
      Incoming payload dictionary that may contain `_H_SEND_EMAIL` and the
      private email metadata keys.

    Returns
    -------
    dict or None
      Shallow copy of the original payload that remains queued for deferred
      processing when email delivery was requested, otherwise ``None`` so the
      base heavy-op can skip queueing entirely.
    """
    if not payload.get(ct.SEND_EMAIL, False):
      return None

    dct = payload.copy()
    # Keep the live payload clean for downstream consumers while the queued
    # copy retains the original notification context and witness image.
    payload.pop(ct.SEND_EMAIL, False)
    payload.pop("_H_EMAIL_CONFIG", None)
    payload.pop("_H_EMAIL_MESSAGE", None)
    payload.pop("_H_EMAIL_SUBJECT", None)
    return dct

  @staticmethod
  def _normalize_provider(provider):
    """Normalize a provider name into a dispatch token.

    Parameters
    ----------
    provider : Any
      Provider identifier from the email configuration.

    Returns
    -------
    str
      Lowercased provider token with ``-`` and ``.`` rewritten as ``_`` so it
      can be mapped to a handler method name.
    """
    provider = str(provider or "").lower()
    return provider.replace("-", "_").replace(".", "_")

  @staticmethod
  def _normalize_recipients(value):
    """Normalize recipient fields for the provider payload.

    Parameters
    ----------
    value : Any
      Raw value from the flat email configuration.

    Returns
    -------
    None or list or str
      ``None`` when the field is empty, a list when the config already
      provides a sequence, or the original scalar otherwise.
    """
    if value is None:
      return None
    if isinstance(value, (list, tuple)):
      return list(value)
    return value

  @staticmethod
  def _build_compact_json_message(dct):
    """Build a compact fallback body from the queued payload.

    Parameters
    ----------
    dct : dict
      Queued payload dictionary that may still contain private heavy-op keys
      and witness images.

    Returns
    -------
    str
      Compact JSON string that excludes private `_H_*` keys and image fields.
    """
    message_payload = {
      key: value
      for key, value in dct.items()
      if not key.startswith("_H_") and key not in {"IMG", "IMG_ORIG"}
    }
    return json.dumps(message_payload, separators=(",", ":"), sort_keys=True)

  @staticmethod
  def _build_witness_attachments(img_value):
    """Translate queued witness images into Resend attachment dictionaries.

    Parameters
    ----------
    img_value : Any
      Either a single witness image or a list/tuple of witness images.

    Returns
    -------
    list of dict
      Attachment dictionaries with deterministic witness filenames.
    """
    if img_value is None:
      return []

    if isinstance(img_value, (list, tuple)):
      images = list(img_value)
      use_sequence_names = True
    else:
      images = [img_value]
      use_sequence_names = False

    attachments = []
    for index, image in enumerate(images, start=1):
      if use_sequence_names:
        filename = "witness_{:02d}.jpg".format(index)
      else:
        filename = "witness.jpg"
      attachments.append({
        "filename": filename,
        "content": image,
      })
    return attachments

  def _process_dct_operation(self, dct):
    """Dispatch queued notification work to a provider-specific sender.

    Parameters
    ----------
    dct : dict
      Queued payload copy returned by `_register_payload_operation()`.

    Returns
    -------
    None
      The method performs side effects only. Provider-specific handlers are
      responsible for the HTTP delivery.
    """
    bool_send_email = dct.get(ct.SEND_EMAIL, False)
    email_config = dct.get("_H_EMAIL_CONFIG", None)
    subject = dct.get("_H_EMAIL_SUBJECT", None)

    if not bool_send_email or email_config is None or subject is None:
      return

    provider_token = self._normalize_provider(email_config.get("PROVIDER", "resend"))
    handler = getattr(self, "_send_{}".format(provider_token), None)
    if handler is None:
      raise ValueError("Unsupported email provider '{}'".format(provider_token))

    message = dct.get("_H_EMAIL_MESSAGE", None)
    if message is None:
      # The queued payload still contains the witness image, but the fallback
      # body must remain compact and must not leak private heavy-op keys.
      message = self._build_compact_json_message(dct)

    handler(
      api_key=email_config.get("API_KEY", ""),
      sender=email_config.get("FROM", ""),
      to=self._normalize_recipients(email_config.get("TO", None)),
      cc=self._normalize_recipients(email_config.get("CC", None)),
      bcc=self._normalize_recipients(email_config.get("BCC", None)),
      reply_to=email_config.get("REPLY_TO", None),
      subject=subject,
      message=message,
      attachments=self._build_witness_attachments(dct.get("IMG", None)),
      provider=provider_token,
    )
    return

  def _send_resend(self, api_key, sender, to, cc, bcc, reply_to, subject, message, attachments, provider=None):
    """Send a single email through the Resend HTTP API.

    Parameters
    ----------
    api_key : str
      Resend API key used for the bearer authorization header.
    sender : str
      Sender address used for the Resend ``from`` field.
    to : list or str
      Primary recipients.
    cc : list or str or None
      Carbon-copy recipients.
    bcc : list or str or None
      Blind-carbon-copy recipients.
    reply_to : str or list or None
      Reply-to address or addresses.
    subject : str
      Email subject line.
    message : str
      Email body. This is typically explicit notification text or a compact
      JSON fallback.
    attachments : list of dict
      Witness image attachments built from the queued payload copy.
    provider : str, optional
      Normalized provider token used for routing. The sender does not need it
      for transport, but the value is useful for tests and traceability.

    Returns
    -------
    requests.Response
      The successful HTTP response returned by Resend.
    """
    _ = provider

    payload = {
      "from": sender,
      "to": to,
      "subject": subject,
      "text": message,
    }
    if cc is not None:
      payload["cc"] = cc
    if bcc is not None:
      payload["bcc"] = bcc
    if reply_to is not None:
      payload["reply_to"] = reply_to
    if attachments:
      payload["attachments"] = attachments

    headers = {
      "Authorization": "Bearer {}".format(api_key),
      "Content-Type": "application/json",
    }
    response = requests.post(
      _RESEND_EMAILS_URL,
      headers=headers,
      json=payload,
      timeout=30,
    )
    response.raise_for_status()
    return response
