# TODO Bleo: WIP
import torch.nn as nn
from naeural_core.local_libraries.nn.th.encoders import SimpleImageEncoder, SimpleImageDecoder, BASIC_ENCODER

_CONFIG = {
  'GRID_SEARCH' : {
    'GRID' : {
      'in_channels' : [3],
      'prc_noise' : [
        0.3,
        0.5
      ],
      'image_size': [
        # [32, 32],
        # [128, 256],
        [448, 640]
      ],
      'layers': [None]
    },
    'CALLBACKS_PARAMS': ['prc_noise'],
    'DATA_PARAMS': ['image_size'],
    'MODEL_PARAMS': ['in_channels', 'image_size', 'layers'],
  }
}


class AutoencoderModelFactory(nn.Module):

  def __init__(self, in_channels=3, image_size=None, layers=None, **kwargs):
    super().__init__()
    layers = layers or BASIC_ENCODER
    image_size = image_size or [64, 64]
    self.in_channels = in_channels
    self.image_size = image_size

    self.encoder = SimpleImageEncoder(
      h=self.image_size[0],
      w=self.image_size[1],
      channels=self.in_channels,
      layers=layers
    )
    self.decoder = nn.Sequential(
      SimpleImageDecoder(
        h=self.image_size[0],
        w=self.image_size[1],
        channels=self.in_channels,
        layers=layers
      ),
      nn.Sigmoid()
    )
    return

  def forward(self, x):
    x = self.encoder(x)
    x = self.decoder(x)
    return x

