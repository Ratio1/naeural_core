"""Standalone regressions for heavy-op notification queueing semantics."""

from collections import deque
import hashlib
import json
from pathlib import Path
import sys
import unittest

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(_PACKAGE_ROOT) not in sys.path:
  sys.path.insert(0, str(_PACKAGE_ROOT))

from naeural_core.heavy_ops.base.base_heavy_op import BaseHeavyOp
from naeural_core.heavy_ops.default.send_mail import SendMailHeavyOp
from naeural_core.heavy_ops.default import send_mail as send_mail_mod
from naeural_core.heavy_ops.default.send_sms import SendSMSHeavyOp
from naeural_core.heavy_ops.default import send_sms as send_sms_mod
from naeural_core import constants as ct


class _NullRegisterHeavyOp(BaseHeavyOp):
  """Heavy-op harness that always reports no work.

  This helper exists solely to exercise the sentinel contract on
  `BaseHeavyOp.process_payload()`. The harness satisfies the abstract base
  class requirements while ensuring that registration consistently returns
  `None` for the intentional no-work case and raises for the error path so
  the regression can verify both outcomes without monkeypatching.
  """

  def _register_payload_operation(self, payload):
    """Return the no-work sentinel or raise for the explicit error path.

    Parameters
    ----------
    payload : dict
      Incoming payload dictionary. The harness inspects the optional
      ``cause_error`` flag so the test can exercise both the no-work and
      exception paths through the real subclass method.

    Returns
    -------
    None
      Explicit sentinel indicating that the caller should skip both queueing
      and synchronous execution.

    Raises
    ------
    RuntimeError
      Raised when ``payload['cause_error']`` is truthy so the caller can verify
      that registration failures remain separate from intentional no-work.
    """
    if payload.get("cause_error"):
      raise RuntimeError("forced registration failure for regression coverage")
    _ = payload
    return None

  def _process_dct_operation(self, dct):
    """Raise if the no-work sentinel ever reaches execution.

    Parameters
    ----------
    dct : Any
      Registered payload data. In this harness, receiving any value here would
      mean the sentinel guard in `process_payload()` failed.

    Raises
    ------
    AssertionError
      Always raised so the test fails immediately if the sentinel is not
      filtered before execution.
    """
    raise AssertionError("None payloads must never reach `_process_dct_operation`.")


class _RecorderHeavyOp(BaseHeavyOp):
  """Heavy-op harness that records processed payload dictionaries.

  The recorder provides a small concrete subclass for the synchronous code
  path. It converts an incoming payload into a derived dictionary and stores
  the processed result so the regression can assert that real work still
  executes normally.
  """

  def __init__(self, *args, **kwargs):
    """Initialize the recorder with an empty capture list.

    Parameters
    ----------
    *args : tuple
      Positional arguments forwarded to `BaseHeavyOp.__init__()`.

    **kwargs : dict
      Keyword arguments forwarded to `BaseHeavyOp.__init__()`.
    """
    self.processed = []
    super().__init__(*args, **kwargs)

  def _register_payload_operation(self, payload):
    """Convert the incoming payload into the derived heavy-op dictionary.

    Parameters
    ----------
    payload : dict
      Source payload whose ``value`` field is copied into the registered
      operation.

    Returns
    -------
    dict
      New dictionary representing the work item that should be executed.
    """
    return {"copied": payload["value"]}

  def _process_dct_operation(self, dct):
    """Store the processed dictionary for later assertions.

    Parameters
    ----------
    dct : dict
      Registered payload dictionary produced by `_register_payload_operation()`.
    """
    self.processed.append(dct)


def test_process_payload_skips_none_registration():
  """Verify that `None` registration results are treated as no-op work.

  The test asserts that a registration result of ``None`` does not reach the
  async queue, does not increment the heavy-op counter, and that a registration
  exception still emits the notification stub without enqueueing work.
  """
  op = object.__new__(_NullRegisterHeavyOp)
  op._ops = deque(maxlen=10000)
  op.comm_async = True
  op.heavy_op_count = 0
  notifications = []

  class _FailureLog:
    """Minimal logger stub that satisfies the error-reporting contract.

    Notes
    -----
    The real heavy-op base class only needs `get_error_info(return_err_val=True)`
    for the registration-failure notification branch. This stub returns a
    deterministic tuple so the regression can assert that the notification path
    executed without requiring the full logging stack.
    """

    def get_error_info(self, return_err_val=True):
      """Return a deterministic error tuple for notification payloads.

      Parameters
      ----------
      return_err_val : bool, optional
        Included for interface compatibility with the live logger. The stub
        ignores the value because the regression only needs a stable tuple.

      Returns
      -------
      tuple
        Five-item error tuple consumed by `BaseHeavyOp.__err_dict()`.
      """
      _ = return_err_val
      return ("RuntimeError", "test_notification_heavy_ops.py", "_register_payload_operation", 1, "forced failure")

  op.log = _FailureLog()
  op._create_notification = lambda **kwargs: notifications.append(kwargs)

  op.process_payload({"value": 1})
  op.process_payload({"cause_error": True})

  assert list(op._ops) == []
  assert op.heavy_op_count == 0
  assert len(notifications) == 1
  assert notifications[0]["notif"] == "EXCEPTION"
  assert "Error in heavy payload operation" in notifications[0]["msg"]


def test_process_payload_runs_sync_only_for_real_work():
  """Verify that real work still executes when async processing is disabled.

  The test checks the synchronous branch of `BaseHeavyOp.process_payload()`
  and ensures that a non-`None` registration result is executed immediately
  and counted exactly once.
  """
  op = object.__new__(_RecorderHeavyOp)
  op._ops = deque(maxlen=10000)
  op.comm_async = False
  op.heavy_op_count = 0
  op.processed = []

  op.process_payload({"value": 7})

  assert op.processed == [{"copied": 7}]
  assert op.heavy_op_count == 1


def test_send_mail_register_scrubs_live_payload_and_keeps_queued_copy():
  """Verify registration scrubs the live payload while preserving the queue copy.

  The regression locks down the heavy-op boundary semantics:
  `_register_payload_operation()` must remove the private email fields from the
  live payload dictionary that keeps flowing through the pipeline, but it must
  return a queued copy that still contains the original witness image payload
  for deferred delivery.
  """
  op = object.__new__(SendMailHeavyOp)
  payload = {
    ct.SEND_EMAIL: True,
    "_H_EMAIL_CONFIG": {"PROVIDER": "resend", "API_KEY": "k", "FROM": "from@example.com", "TO": ["to@example.com"]},
    "_H_EMAIL_MESSAGE": "message",
    "_H_EMAIL_SUBJECT": "subject",
    "IMG": "witness-image",
    "VALUE": 1,
  }

  queued = op._register_payload_operation(payload)

  assert queued is not payload
  assert ct.SEND_EMAIL not in payload
  assert "_H_EMAIL_CONFIG" not in payload
  assert "_H_EMAIL_MESSAGE" not in payload
  assert "_H_EMAIL_SUBJECT" not in payload
  assert payload["IMG"] == "witness-image"
  assert queued[ct.SEND_EMAIL] is True
  assert queued["_H_EMAIL_CONFIG"]["PROVIDER"] == "resend"
  assert queued["IMG"] == "witness-image"
  assert queued["VALUE"] == 1


def test_send_mail_register_skips_payloads_without_email_flag():
  """Verify ordinary payloads do not enter the email heavy-op queue.

  The mail heavy-op shares the async dispatch path with every payload emitted by
  the orchestrator. When the email control flag is absent, registration must
  therefore return ``None`` so the base heavy-op can skip queueing entirely.
  """
  op = object.__new__(SendMailHeavyOp)
  payload = {
    "VALUE": 1,
    "IMG": "witness-image",
  }

  queued = op._register_payload_operation(payload)

  assert queued is None
  assert payload == {
    "VALUE": 1,
    "IMG": "witness-image",
  }


def test_send_mail_dispatches_to_provider_slug_and_builds_attachments():
  """Verify provider-slug dispatch and witness attachment synthesis.

  The regression exercises the full heavy-op execution path. It registers the
  payload, keeps the queued witness image intact, routes the provider string
  through slug normalization, and stubs the Resend transport layer so the test
  can assert the actual HTTP contract, including the fallback JSON body when no
  explicit `_H_EMAIL_MESSAGE` exists.
  """
  class _FakeResponse:
    """Minimal fake response that records `raise_for_status()` usage."""

    def __init__(self):
      self.raise_for_status_called = False
      self.status_code = 200
      self.text = '{"id":"email-id"}'

    def raise_for_status(self):
      """Mark the fake response as checked for HTTP errors."""
      self.raise_for_status_called = True

  captured = []

  def _fake_post(url, headers=None, json=None, timeout=None):
    """Record the Resend request contract and return a fake response."""
    response = _FakeResponse()
    captured.append({
      "url": url,
      "headers": headers,
      "json": json,
      "timeout": timeout,
      "response": response,
    })
    return response

  original_post = send_mail_mod.requests.post
  send_mail_mod.requests.post = _fake_post
  try:
    op = object.__new__(SendMailHeavyOp)
    logs = []
    op.P = lambda message, **kwargs: logs.append((message, kwargs))

    payload = {
      ct.SEND_EMAIL: True,
      "_H_EMAIL_CONFIG": {
        "PROVIDER": "Resend",
        "API_KEY": "test-api-key",
        "FROM": "alerts@example.com",
        "TO": ["ops@example.com"],
        "CC": ["cc@example.com"],
        "BCC": ["bcc@example.com"],
        "REPLY_TO": "reply@example.com",
      },
      "_H_EMAIL_SUBJECT": "Alert subject",
      "STREAM": "stream-a",
      "VALUE": 7,
      "IMG": ["image-01", "image-02"],
      "IMG_ORIG": "original-image",
    }

    queued = op._register_payload_operation(payload)
    op._process_dct_operation(queued)
  finally:
    send_mail_mod.requests.post = original_post

  assert queued["IMG"] == ["image-01", "image-02"]
  assert len(captured) == 1

  call = captured[0]
  assert call["url"] == "https://api.resend.com/emails"
  assert call["headers"] == {
    "Authorization": "Bearer test-api-key",
    "Content-Type": "application/json",
  }
  assert call["timeout"] == 30
  assert call["response"].raise_for_status_called is True
  assert call["json"] == {
    "from": "alerts@example.com",
    "to": ["ops@example.com"],
    "cc": ["cc@example.com"],
    "bcc": ["bcc@example.com"],
    "reply_to": "reply@example.com",
    "subject": "Alert subject",
    "text": json.dumps({"STREAM": "stream-a", "VALUE": 7}, separators=(",", ":"), sort_keys=True),
    "attachments": [
      {"filename": "witness_01.jpg", "content": "image-01"},
      {"filename": "witness_02.jpg", "content": "image-02"},
    ],
  }

  fallback_body = json.loads(call["json"]["text"])
  assert fallback_body == {
    "STREAM": "stream-a",
    "VALUE": 7,
  }
  assert "IMG" not in fallback_body
  assert "IMG_ORIG" not in fallback_body
  assert "_H_EMAIL_CONFIG" not in fallback_body
  assert "_H_EMAIL_SUBJECT" not in fallback_body
  assert "_H_SEND_EMAIL" not in fallback_body
  assert len(logs) == 2
  assert "EMAIL_SEND_ATTEMPT" in logs[0][0]
  assert "provider=resend" in logs[0][0]
  assert "EMAIL_SEND_RESPONSE" in logs[1][0]
  assert "status_code=200" in logs[1][0]
  assert "email-id" in logs[1][0]
  assert "test-api-key" not in logs[0][0]
  assert "test-api-key" not in logs[1][0]


def test_send_sms_register_scrubs_live_payload():
  """Verify SMS registration scrubs the live payload and preserves queue state.

  The regression locks down the heavy-op boundary semantics for SMS delivery:
  `_register_payload_operation()` must remove the SMS control fields from the
  live payload dictionary that continues through the pipeline, while returning
  a shallow queued copy that still carries the notification fields needed for
  deferred delivery.
  """
  op = object.__new__(SendSMSHeavyOp)
  payload = {
    ct.SEND_SMS: True,
    "_H_SMS_CONFIG": {
      "PROVIDER": "web2sms",
      "API_KEY": "api-key",
      "SIGNATURE": "secret",
      "SENDER": "alerts",
      "TO": ["+40711111111", "+40722222222"],
    },
    "_H_SMS_MESSAGE": "System alert",
    "IMG": "witness-image",
    "IMG_ORIG": "original-image",
    "VALUE": 17,
  }

  queued = op._register_payload_operation(payload)

  assert queued is not payload
  assert ct.SEND_SMS not in payload
  assert "_H_SMS_CONFIG" not in payload
  assert "_H_SMS_MESSAGE" not in payload
  assert payload["IMG"] == "witness-image"
  assert payload["IMG_ORIG"] == "original-image"
  assert queued[ct.SEND_SMS] is True
  assert queued["_H_SMS_CONFIG"]["PROVIDER"] == "web2sms"
  assert queued["_H_SMS_CONFIG"]["TO"] == ["+40711111111", "+40722222222"]
  assert queued["_H_SMS_MESSAGE"] == "System alert"
  assert queued["IMG"] == "witness-image"
  assert queued["IMG_ORIG"] == "original-image"
  assert queued["VALUE"] == 17


def test_send_sms_dispatches_each_recipient():
  """Verify SMS delivery dispatches one HTTP request per recipient.

  The regression exercises the real provider path by stubbing only
  `requests.post()` and `time.time()` on the SMS module. That keeps the
  production `_send_web2sms()` implementation active so the test can assert
  the exact payload contract, including the nonce/signature concatenation and
  the rule that witness images do not influence SMS delivery.
  """
  class _FakeResponse:
    """Minimal fake response that records `raise_for_status()` usage."""

    def __init__(self):
      self.raise_for_status_called = False
      self.status_code = 201
      self.text = '{"id":"sms-id","error":{"code":0}}'

    def raise_for_status(self):
      """Mark the fake response as checked for HTTP errors."""
      self.raise_for_status_called = True

  captured = []

  def _fake_post(url, auth=None, json=None, timeout=None):
    """Record the web2sms request contract and return a fake response."""
    response = _FakeResponse()
    captured.append({
      "url": url,
      "auth": auth,
      "json": json,
      "timeout": timeout,
      "response": response,
    })
    return response

  original_post = send_sms_mod.requests.post
  original_time = send_sms_mod.time.time
  send_sms_mod.requests.post = _fake_post
  send_sms_mod.time.time = lambda: 1730000000
  try:
    op = object.__new__(SendSMSHeavyOp)
    logs = []
    op.P = lambda message, **kwargs: logs.append((message, kwargs))

    payload = {
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "Web2SMS",
        "API_KEY": "test-api-key",
        "SIGNATURE": "secret-value",
        "SENDER": "alerts",
        "TO": ["+40711111111", "+40722222222"],
      },
      "_H_SMS_MESSAGE": "System alert",
      "STREAM": "stream-a",
      "VALUE": 7,
      "IMG": "witness-image",
      "IMG_ORIG": "original-image",
    }

    queued = op._register_payload_operation(payload)
    op._process_dct_operation(queued)
  finally:
    send_sms_mod.requests.post = original_post
    send_sms_mod.time.time = original_time

  assert queued["IMG"] == "witness-image"
  assert queued["IMG_ORIG"] == "original-image"
  assert len(captured) == 2

  expected_recipients = ["+40711111111", "+40722222222"]
  for index, call in enumerate(captured):
    recipient = expected_recipients[index]
    expected_signature = hashlib.sha512(
      (
        "test-api-key"
        + "1730000000"
        + "POST"
        + "/prepaid/message"
        + "alerts"
        + recipient
        + "System alert"
        + "System alert"
        + ""
        + ""
        + ""
        + "secret-value"
      ).encode("utf-8")
    ).hexdigest()

    assert call["url"] == "https://www.web2sms.ro/prepaid/message"
    assert call["auth"] == ("test-api-key", expected_signature)
    assert call["timeout"] == 30
    assert call["response"].raise_for_status_called is True
    assert call["json"] == {
      "apiKey": "test-api-key",
      "sender": "alerts",
      "recipient": recipient,
      "message": "System alert",
      "visibleMessage": "System alert",
      "nonce": "1730000000",
      "signature": expected_signature,
    }
    assert "IMG" not in call["json"]
    assert "IMG_ORIG" not in call["json"]

  assert len(logs) == 4
  assert "SMS_SEND_ATTEMPT" in logs[0][0]
  assert "provider=web2sms" in logs[0][0]
  assert "+40711111111" not in logs[0][0]
  assert "SMS_SEND_RESPONSE" in logs[1][0]
  assert "status_code=201" in logs[1][0]
  assert "sms-id" in logs[1][0]
  assert "test-api-key" not in logs[0][0]
  assert "secret-value" not in logs[0][0]
  assert expected_signature not in logs[1][0]


def test_send_sms_allows_blank_sender_for_web2sms():
  """Verify web2sms accepts a blank sender field.

  The provider contract marks `sender` as optional, so the runtime must keep
  the empty slot in the signature source instead of failing before the HTTP
  request is issued. This regression locks down that provider-specific rule.
  """
  class _FakeResponse:
    """Minimal fake response that records `raise_for_status()` usage."""

    def __init__(self):
      self.raise_for_status_called = False

    def raise_for_status(self):
      """Mark the fake response as checked for HTTP errors."""
      self.raise_for_status_called = True

  captured = []

  def _fake_post(url, auth=None, json=None, timeout=None):
    """Record the web2sms request contract and return a fake response."""
    response = _FakeResponse()
    captured.append({
      "url": url,
      "auth": auth,
      "json": json,
      "timeout": timeout,
      "response": response,
    })
    return response

  original_post = send_sms_mod.requests.post
  original_time = send_sms_mod.time.time
  send_sms_mod.requests.post = _fake_post
  send_sms_mod.time.time = lambda: 1730000001
  try:
    op = object.__new__(SendSMSHeavyOp)
    op.P = lambda message, **kwargs: None
    op._process_dct_operation({
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "web2sms",
        "API_KEY": "test-api-key",
        "SIGNATURE": "secret-value",
        "SENDER": "   ",
        "TO": ["+40711111111"],
      },
      "_H_SMS_MESSAGE": "Sender optional test",
    })
  finally:
    send_sms_mod.requests.post = original_post
    send_sms_mod.time.time = original_time

  assert len(captured) == 1
  call = captured[0]
  expected_signature = hashlib.sha512(
    (
      "test-api-key"
      + "1730000001"
      + "POST"
      + "/prepaid/message"
      + ""
      + "+40711111111"
      + "Sender optional test"
      + "Sender optional test"
      + ""
      + ""
      + ""
      + "secret-value"
    ).encode("utf-8")
  ).hexdigest()

  assert call["url"] == "https://www.web2sms.ro/prepaid/message"
  assert call["auth"] == ("test-api-key", expected_signature)
  assert call["timeout"] == 30
  assert call["response"].raise_for_status_called is True
  assert call["json"] == {
    "apiKey": "test-api-key",
    "sender": "",
    "recipient": "+40711111111",
    "message": "Sender optional test",
    "visibleMessage": "Sender optional test",
    "nonce": "1730000001",
    "signature": expected_signature,
  }


def test_send_sms_rejects_blank_mandatory_values():
  """Verify blank mandatory SMS values fail before any outbound request.

  The regression protects the transport boundary from emitting HTTP requests
  with empty credentials, recipient entries, or message content.
  Each case must raise locally and leave the request stub untouched.
  """
  op = object.__new__(SendSMSHeavyOp)
  invalid_cases = [
    ("blank api key", {
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "web2sms",
        "API_KEY": "   ",
        "SIGNATURE": "secret-value",
        "SENDER": "alerts",
        "TO": ["+40711111111"],
      },
      "_H_SMS_MESSAGE": "System alert",
    }),
    ("blank signature", {
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "web2sms",
        "API_KEY": "test-api-key",
        "SIGNATURE": "",
        "SENDER": "alerts",
        "TO": ["+40711111111"],
      },
      "_H_SMS_MESSAGE": "System alert",
    }),
    ("blank recipient", {
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "web2sms",
        "API_KEY": "test-api-key",
        "SIGNATURE": "secret-value",
        "SENDER": "alerts",
        "TO": ["+40711111111", " "],
      },
      "_H_SMS_MESSAGE": "System alert",
    }),
    ("blank message", {
      ct.SEND_SMS: True,
      "_H_SMS_CONFIG": {
        "PROVIDER": "web2sms",
        "API_KEY": "test-api-key",
        "SIGNATURE": "secret-value",
        "SENDER": "alerts",
        "TO": ["+40711111111"],
      },
      "_H_SMS_MESSAGE": "   ",
    }),
  ]

  original_post = send_sms_mod.requests.post
  captured = []

  def _fail_post(*args, **kwargs):
    captured.append((args, kwargs))
    raise AssertionError("Outbound request should not be attempted for blank SMS values")

  send_sms_mod.requests.post = _fail_post
  try:
    for label, payload in invalid_cases:
      queued = op._register_payload_operation(payload)
      try:
        op._process_dct_operation(queued)
        raise AssertionError("Expected ValueError for {}".format(label))
      except ValueError:
        pass
  finally:
    send_sms_mod.requests.post = original_post

  assert captured == []


TEST_FUNCTIONS = (
  test_process_payload_skips_none_registration,
  test_process_payload_runs_sync_only_for_real_work,
  test_send_mail_register_scrubs_live_payload_and_keeps_queued_copy,
  test_send_mail_register_skips_payloads_without_email_flag,
  test_send_mail_dispatches_to_provider_slug_and_builds_attachments,
  test_send_sms_register_scrubs_live_payload,
  test_send_sms_dispatches_each_recipient,
  test_send_sms_allows_blank_sender_for_web2sms,
  test_send_sms_rejects_blank_mandatory_values,
)


def load_tests(loader, tests, pattern):
  """Return the seven standalone regression functions as discoverable test cases.

  Parameters
  ----------
  loader : unittest.TestLoader
    Standard unittest loader provided by discovery.

  tests : unittest.TestSuite
    Existing suite assembled by discovery before this module hook runs.

  pattern : str
    Discovery filename pattern. The module keeps this parameter for unittest
    compatibility, but it does not need to inspect it directly.

  Returns
  -------
  unittest.TestSuite
    Suite containing `FunctionTestCase` wrappers for the current regression
    functions so `unittest discover` can execute them.
  """
  _ = loader, tests, pattern
  suite = unittest.TestSuite()
  suite.addTests([unittest.FunctionTestCase(test_func) for test_func in TEST_FUNCTIONS])
  return suite


def _run_all_tests():
  """Run the standalone notification heavy-op regression checks.

  Returns
  -------
  None
    The helper is a thin manual test runner for direct script execution.
  """
  for test_func in TEST_FUNCTIONS:
    test_func()


if __name__ == "__main__":
  _run_all_tests()
