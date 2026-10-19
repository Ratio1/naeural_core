"""Cover plugin-facing dAuth calls through the real blockchain wrapper."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from naeural_core.utils.plugins_base.bc_wrapper import BCWrapper


class BCWrapperDauthTests(unittest.TestCase):
  def test_verify_preserves_defaults_and_forwards_sensitive_logging_option(self):
    """Keep legacy callers working and allow suppressing secret-bearing logs."""
    engine = SimpleNamespace(verify=Mock(return_value=SimpleNamespace(valid=True)))
    wrapper = BCWrapper(engine, owner=None)
    body = {"EE_SENDER": "requester"}
    for log_hash_sign_fails in (True, False):
      with self.subTest(log_hash_sign_fails=log_hash_sign_fails):
        kwargs = {} if log_hash_sign_fails else {"log_hash_sign_fails": False}
        result = wrapper.verify(body, str_signature="signature", sender_address="sender", **kwargs)
        self.assertTrue(result.valid)
        engine.verify.assert_called_with(
          dct_data=body, signature="signature", sender_address="sender",
          return_full_info=True, log_hash_sign_fails=log_hash_sign_fails,
        )

    wrapper.verify(body, "signature", "sender", False)
    engine.verify.assert_called_with(
      dct_data=body, signature="signature", sender_address="sender",
      return_full_info=False, log_hash_sign_fails=True,
    )

  def test_reconciliation_forwards_payload_network_and_timeout_to_sdk(self):
    """Expose the SDK client through the object plugins actually receive."""
    result = {"status": "success", "pipeline_cid": "cid"}
    engine = SimpleNamespace(reconcile_dauth_job_secrets=Mock(return_value=result))
    wrapper = BCWrapper(engine, owner=None)
    payload = {"job_id": "74", "pipeline_configs": {"worker": {}}}
    self.assertIs(wrapper.reconcile_dauth_job_secrets(payload), result)
    engine.reconcile_dauth_job_secrets.assert_called_once_with(
      payload=payload, network=None, request_timeout=(10, 60),
    )
    self.assertIs(wrapper.reconcile_dauth_job_secrets(payload, network="testnet", request_timeout=(2, 5)), result)
    engine.reconcile_dauth_job_secrets.assert_called_with(
      payload=payload, network="testnet", request_timeout=(2, 5),
    )


if __name__ == "__main__":
  unittest.main()
