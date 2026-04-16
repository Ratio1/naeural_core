"""Heavy-op SMS delivery with provider-slug dispatch."""

import hashlib
import time

import requests

from naeural_core.heavy_ops.base import BaseHeavyOp
from naeural_core import constants as ct


CONFIG = {
  "IDLE_THREAD_SLEEP_TIME": 2,
}

_WEB2SMS_URL = "https://www.web2sms.ro/prepaid/message"


class SendSMSHeavyOp(BaseHeavyOp):
  """Deliver SMS notifications from queued heavy-op payloads.

  Notes
  -----
  The SMS workflow keeps its payload contract intentionally flat. The live
  payload carries the control keys ``_H_SEND_SMS``, ``_H_SMS_CONFIG``, and
  ``_H_SMS_MESSAGE`` only long enough for registration to enqueue the work;
  after that, the heavy-op scrubs those fields from the live payload so the
  rest of the pipeline continues without SMS-specific state. Delivery then
  fans out one HTTP request per recipient using the provider token in the SMS
  config.
  """

  def _register_payload_operation(self, payload):
    """Return a queued copy when SMS work is requested.

    Parameters
    ----------
    payload : dict
      Incoming payload dictionary that may contain ``_H_SEND_SMS`` and the
      SMS control keys.

    Returns
    -------
    dict or None
      Shallow copy of the payload when SMS delivery is requested, otherwise
      ``None`` so the payload continues through the pipeline without SMS work.
    """
    if not payload.get(ct.SEND_SMS, False):
      return None

    dct = payload.copy()
    # Keep the live payload clean for downstream consumers while the queued
    # copy retains the original notification context.
    payload.pop(ct.SEND_SMS, False)
    payload.pop("_H_SMS_CONFIG", None)
    payload.pop("_H_SMS_MESSAGE", None)
    return dct

  @staticmethod
  def _require_non_blank_text(value, field_name):
    """Validate a required text value and return its trimmed form.

    Parameters
    ----------
    value : Any
      Raw value from the incoming SMS payload or config.
    field_name : str
      Human-readable field name used in the error message.

    Returns
    -------
    str
      Trimmed string value when the input is present and non-blank.

    Raises
    ------
    ValueError
      Raised when the value is missing or contains only whitespace.
    """
    text = str(value or "").strip()
    if text == "":
      raise ValueError("SMS delivery requested but `{}` is blank".format(field_name))
    return text

  @staticmethod
  def _normalize_provider(provider):
    """Normalize a provider token into a Python handler suffix.

    Parameters
    ----------
    provider : Any
      Raw provider identifier from the SMS config.

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
    """Normalize the SMS recipient field into a list of recipients.

    Parameters
    ----------
    value : Any
      Raw value from the SMS config.

    Returns
    -------
    list
      Recipient list that is safe to iterate over when sending one request
      per destination.
    """
    if value is None:
      return []
    if isinstance(value, (list, tuple)):
      values = list(value)
    else:
      values = [value]

    recipients = []
    for index, recipient in enumerate(values):
      recipient_text = str(recipient or "").strip()
      if recipient_text == "":
        raise ValueError("SMS delivery requested but recipient #{} is blank".format(index + 1))
      recipients.append(recipient_text)
    return recipients

  def _process_dct_operation(self, dct):
    """Validate and dispatch SMS delivery work.

    Parameters
    ----------
    dct : dict
      Queued payload dictionary returned by `_register_payload_operation()`.

    Returns
    -------
    None
      The method performs side effects only and raises on invalid payloads or
      unsupported providers.
    """
    bool_send_sms = dct.get(ct.SEND_SMS, False)
    if not bool_send_sms:
      return

    sms_config = dct.get("_H_SMS_CONFIG", None)
    sms_message = dct.get("_H_SMS_MESSAGE", None)
    if sms_config is None:
      raise ValueError("SMS delivery requested but `_H_SMS_CONFIG` is missing")
    if sms_message is None:
      raise ValueError("SMS delivery requested but `_H_SMS_MESSAGE` is missing")
    if not isinstance(sms_config, dict):
      raise TypeError("SMS config must be a dictionary")

    api_key = self._require_non_blank_text(sms_config.get(ct.SMS_NOTIFICATION.API_KEY, None), ct.SMS_NOTIFICATION.API_KEY)
    signature = self._require_non_blank_text(sms_config.get(ct.SMS_NOTIFICATION.SIGNATURE, None), ct.SMS_NOTIFICATION.SIGNATURE)
    sender = self._require_non_blank_text(sms_config.get(ct.SMS_NOTIFICATION.SENDER, None), ct.SMS_NOTIFICATION.SENDER)
    sms_message = self._require_non_blank_text(sms_message, "_H_SMS_MESSAGE")
    recipients = self._normalize_recipients(sms_config.get(ct.SMS_NOTIFICATION.TO, None))
    if len(recipients) == 0:
      raise ValueError("SMS delivery requested but no recipients were configured")

    # Provider tokens are normalized before dispatch so config values such as
    # ``Web2SMS`` or ``web2sms`` map to the same handler method.
    provider_token = self._normalize_provider(sms_config.get(ct.SMS_NOTIFICATION.PROVIDER, "web2sms"))
    handler = getattr(self, "_send_{}".format(provider_token), None)
    if handler is None:
      raise ValueError("Unsupported SMS provider '{}'".format(provider_token))

    for recipient in recipients:
      handler(
        dct,
        sms_config,
        recipient,
        sms_message,
        api_key=api_key,
        signature=signature,
        sender=sender,
      )
    return

  def _send_web2sms(self, dct, config, recipient, message, api_key=None, signature=None, sender=None):
    """Send a single SMS through the web2sms HTTP API.

    Parameters
    ----------
    dct : dict
      Queued payload dictionary. The SMS transport ignores witness media
      fields such as ``IMG`` and ``IMG_ORIG``.
    config : dict
      SMS configuration dictionary containing the provider credentials and
      recipient defaults.
    recipient : str
      Single recipient phone number to deliver to.
    message : str
      SMS message body. The same text is used for both the message payload and
      the visible message field expected by the provider.
    api_key : str, optional
      Trimmed API key used both in the payload and as the HTTP Basic auth
      username.
    signature : str, optional
      Trimmed signature secret used as the HTTP Basic auth password.
    sender : str, optional
      Trimmed sender identifier that is echoed in the request body and signed
      by the provider contract.

    Returns
    -------
    requests.Response
      Successful HTTP response returned by the provider.
    """
    _ = dct

    # Validate again at the transport boundary so a direct provider call cannot
    # accidentally bypass the fail-fast guard enforced by `_process_dct_operation()`.
    api_key = self._require_non_blank_text(
      api_key if api_key is not None else config.get(ct.SMS_NOTIFICATION.API_KEY, None),
      ct.SMS_NOTIFICATION.API_KEY,
    )
    sender = self._require_non_blank_text(
      sender if sender is not None else config.get(ct.SMS_NOTIFICATION.SENDER, None),
      ct.SMS_NOTIFICATION.SENDER,
    )
    signature = self._require_non_blank_text(
      signature if signature is not None else config.get(ct.SMS_NOTIFICATION.SIGNATURE, None),
      ct.SMS_NOTIFICATION.SIGNATURE,
    )
    recipient = self._require_non_blank_text(recipient, "recipient")
    message = self._require_non_blank_text(message, "_H_SMS_MESSAGE")
    visible_message = message
    nonce = str(int(time.time()))
    method = "POST"
    uri = "/prepaid/message"
    schedule_date = ""
    validity_date = ""
    callback_url = ""

    # The provider signs the exact concatenation below, so keep the field
    # order and the empty slots stable. Any reordering changes the digest and
    # breaks delivery even when the visible request body looks correct.
    signature_source = (
      api_key
      + nonce
      + method
      + uri
      + sender
      + recipient
      + message
      + visible_message
      + schedule_date
      + validity_date
      + callback_url
      + signature
    )
    signature = hashlib.sha512(signature_source.encode("utf-8")).hexdigest()

    payload = {
      "apiKey": api_key,
      "sender": sender,
      "recipient": recipient,
      "message": message,
      "visibleMessage": visible_message,
      "nonce": nonce,
      "signature": signature,
    }

    # Use HTTP Basic auth for the provider credential layer while preserving
    # the signed JSON body that the remote API still expects for delivery.
    response = requests.post(_WEB2SMS_URL, auth=(api_key, signature), json=payload, timeout=30)
    response.raise_for_status()
    return response
