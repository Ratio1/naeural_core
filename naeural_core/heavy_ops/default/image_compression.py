from naeural_core.utils.img_utils import maybe_prepare_img_payload
from naeural_core.heavy_ops.base import BaseHeavyOp

class ImageCompressionHeavyOp(BaseHeavyOp):

  def __init__(self, **kwargs):
    super(ImageCompressionHeavyOp, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    # should run only in comm thread and not async as it replaces inplace the
    # 'IMG' with the base64 string
    assert not self.comm_async 

  def _register_payload_operation(self, payload):
    """
    this plugin must modify the payload before sending to outside
    """
    return payload # we just return original dict so we can change it inplace

  def _process_dct_operation(self, dct):
    maybe_prepare_img_payload(sender=self, dct=dct)
    return
