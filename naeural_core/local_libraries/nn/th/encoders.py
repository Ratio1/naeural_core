import os
import numpy as np
import torch as th

from naeural_core.local_libraries.nn.utils import conv_output_shape, get_model_size
from naeural_core.local_libraries.nn.th.layers import (
  GlobalMaxPool2d,
  InputPlaceholder,
  ReshapeLayer,
  Conv2dExt,
  ConvTranspose2dExt,
  IdentityLayer,
  AffineTransform,
  SpatialTransformerNet,
)


BASIC_ENCODER = [
  {
   "kernel"  : 3,
   "stride"  : 2,
   "filters" : 32,
   "padding" : 1
  },
  {
   "kernel"  : 3,
   "stride"  : 2,
   "filters" : 128,
   "padding" : 1,
  },
  {
   "kernel"  : 3,
   "stride"  : 1,
   "filters" : None, # this will be auto-calculated for last encoding layer
   "padding" : 1,
  },
]

def calc_embed_size(h, w, c, root=3, scale=1):
  img_size = h * w * c
  v = int(np.power(img_size, 1/root))
  v = v * scale
  # now cosmetics
  vf = int(v / 4) * 4
  return vf


class SimpleImageEncoder(th.nn.Module):
  def __init__(self, h, w, channels,
               root=3, scale=1,
               embed_size=None,
               layers=BASIC_ENCODER):
    super().__init__()
    self.hw = (h, w)
    self.layers = th.nn.ModuleList()
    last_channels = channels
    input_shape = (channels, h, w)
    for layer in layers:
      k = layer.get('kernel', 3)
      s = layer.get('stride', 2)
      p = layer.get('padding', 1)
      f = layer['filters']
      if f is None:
        if embed_size is None:
          f = calc_embed_size(
            h, w,
            c=channels,
            root=root,
            scale=scale
            )
        else:
          f = embed_size
      cnv = Conv2dExt(
        input_shape=input_shape,
        in_channels=last_channels,
        out_channels=f,
        kernel_size=k,
        stride=s,
        padding=p,
        show_input=False,
        )
      input_shape = cnv.output_shape
      last_channels = f
      bn = th.nn.BatchNorm2d(f)
      act = th.nn.ReLU()
      self.layers.append(cnv)
      self.layers.append(bn)
      self.layers.append(act)
    self.embed_layer = GlobalMaxPool2d()
    self.encoder_embed_size = last_channels
    return

  def forward(self, inputs):
    th_x = inputs
    for layer in self.layers:
      th_x = layer(th_x)
      # print('  ',th_x.shape)
    th_out = self.embed_layer(th_x)
    return th_out


class SimpleImageDecoder(th.nn.Module):
  def __init__(self,
               h, w,
               channels,
               embed_size=None,
               root=3,
               scale=1,
               layers=BASIC_ENCODER
               ):
    super().__init__()
    if embed_size is None:
      embed_size = calc_embed_size(
        h, w,
        c=channels,
        root=root,
        scale=scale,
        )
    self.hw = (h, w)
    self.layers = th.nn.ModuleList()
    reduce_layers = len([x['stride'] for x in layers if x['stride'] > 1])
    input_layer = InputPlaceholder((embed_size,))
    expansion_channels = int(embed_size ** 0.5)
    expansion_h = h // (2 ** reduce_layers)
    expansion_w = w // (2 ** reduce_layers)
    expansion_size = expansion_h * expansion_w * expansion_channels
    expansion_layer = th.nn.Linear(embed_size, expansion_size)
    reshape_layer = ReshapeLayer((
      expansion_channels,
      expansion_h, expansion_w
      ))
    input_shape = (expansion_channels, expansion_h, expansion_w)
    self.layers.append(input_layer)
    self.layers.append(expansion_layer)
    self.layers.append(reshape_layer)
    last_channels = expansion_channels
    layers = list(reversed(layers))
    for layer in layers:
      k = layer.get('kernel', 3)
      s = layer.get('stride', 2)
      p = layer.get('padding', 1)
      f = layer['filters']
      if f is None:
        f = embed_size
      if s == 1:
        cnv = Conv2dExt(
          input_shape=input_shape,
          in_channels=last_channels,
          out_channels=f,
          kernel_size=k,
          stride=1,
          padding=p,
          show_input=False,
          )
      else:
        cnv = ConvTranspose2dExt(
          in_channels=last_channels,
          input_shape=input_shape,
          out_channels=f,
          kernel_size=k-1,
          stride=s,
          show_input=False,
          # padding=p
          )
      input_shape = cnv.output_shape
      last_channels = f
      bn = th.nn.BatchNorm2d(f)
      act = th.nn.ReLU()
      self.layers.append(cnv)
      self.layers.append(bn)
      self.layers.append(act)

    self.out_layer = Conv2dExt(
      in_channels=last_channels,
      out_channels=channels,
      kernel_size=1,
      input_shape=input_shape,
      show_input=False,
    )
    return

  def forward(self, inputs):
    th_embed = inputs
    th_x = th_embed
    for layer in self.layers:
      th_x = layer(th_x)
      # print('  ',th_x.shape)
    th_out = self.out_layer(th_x)
    return th_out


class SimpleDomainAutoEncoder(th.nn.Module):
  def __init__(self,
               in_h, in_w,
               out_h, out_w, channels,
               domain_name,
               variable_input=False,
               save_folder='_local_cache/models',
               root=3,
               scale=3,
               embed_size=None,
               layers=BASIC_ENCODER
               ):
    super().__init__()

    self.domain_name = domain_name
    self.save_folder = save_folder

    self.encoder = SimpleImageEncoder(
      h=in_h, w=in_w,
      channels=channels,
      layers=layers,
      root=root,
      scale=scale,
      embed_size=embed_size,
      )

    self.decoder = SimpleImageDecoder(
      embed_size=self.encoder.encoder_embed_size,
      h=out_h, w=out_w,
      channels=channels,
      layers=layers
      )
    return

  def forward(self, inputs):
    th_x = self.encoder(inputs)
    th_out = self.decoder(th_x)
    return th_out


  def save_encoder(self, path=None):
    if path is None:
      os.makedirs(self.save_folder, exists_ok=True)
      path = os.path.join(
        self.save_folder,
        "{}_enc{}.pt".format(
          self.domain_name,
          self.encoder.encoder_embed_size
          )
        )
    in_train = self.encoder.training
    self.encoder.eval()
    th.save(self.encoder.state_dict(), path)
    self.encoder_save_path = path
    if in_train:
      self.encoder.train()
    return

  def save_decoder(self, path=None):
    if path is None:
      os.makedirs(self.save_folder, exists_ok=True)
      path = os.path.join(
        self.save_folder,
        "{}_dec{}.pt".format(
          self.domain_name,
          self.encoder.encoder_embed_size
          )
        )
    in_train = self.decoder.training
    self.decoder.eval()
    th.save(self.decoder.state_dict(), path)
    self.decoder_save_path = path
    if in_train:
      self.decoder.train()
    return

class AdvancedSpatialTransformerNet(th.nn.Module):
  def __init__(
      self,
      input_shape:list=[],
      loc_layers=[(8,7), (10,5)],
      drop_input=0,
      drop_fc=0,
      fc_afine=[32],
      reflection=True,
      # below advanced layers
      decode_reshape=None, # TODO: implement (CHW)
      decode_layers=[], # (f,k)
      decode_act=False,
    ) -> None:
    super().__init__()

    assert len(decode_layers) > 0, "Must specify auto-encoder design with `decode_layers"

    self.stn = SpatialTransformerNet(
      input_shape=input_shape,
      loc_layers=loc_layers,
      drop_input=drop_input,
      drop_fc=drop_fc,
      fc_afine=fc_afine,
      reflection=reflection,
      affine_reshape=input_shape,
    )

    channels = input_shape[0]
    if decode_reshape is None:
      decode_reshape = self.stn.affine_reshape

    if len(decode_reshape) == 2:
      decode_reshape = (channels, ) + tuple(decode_reshape)

    decs = []
    prev_output = (input_shape[0],) + self.stn.affine_reshape[1:]
    prev_f = channels
    for f,k in decode_layers:
      decs.append(
        Conv2dExt(
          input_shape=prev_output,
          in_channels=prev_f,
          out_channels=f,
          kernel_size=k,
          stride=2,
      ))
      prev_output = decs[-1].output_shape
      decs.append(th.nn.ReLU(True))
      prev_f = f
    decs.append(th.nn.Upsample(decode_reshape[1:]))
    decs.append(
      Conv2dExt(
        input_shape=decode_reshape[1:],
        in_channels=prev_f,
        out_channels=f,
        kernel_size=1,
      )
    )
    prev_output = decs[-1].output_shape
    decs.append(th.nn.ReLU(True))


    self.decoder = th.nn.ModuleList(decs)
    if decode_act is not None:
      self.readout_image = th.nn.Sequential(
        Conv2dExt(
          input_shape=prev_output,
          in_channels=prev_f,
          out_channels=channels,
          kernel_size=1
        ),
        th.nn.Sigmoid()
      )
    else:
      self.readout_image = Conv2dExt(
        input_shape=prev_output,
        in_channels=prev_f,
        out_channels=channels,
        kernel_size=1
      )
    return


  def forward(self, inputs):
    th_theta, th_x_img = self.stn(inputs)

    for layer in self.decoder:
      th_x_img = layer(th_x_img)

    th_image = self.readout_image(th_x_img)

    return th_theta, th_image


  def transform(self, images, shape):
    th_thetas, _ = self(images)

    th_transform_matrix = th.nn.functional.affine_grid(th_thetas, shape)

    th_transformed = th.nn.functional.grid_sample(images, th_transform_matrix)
    return th_transformed


if __name__ == "__main__":
  TEST_SIMPLE_DOMAIN_AE = False
  TEST_ADV_STN = True

  if TEST_SIMPLE_DOMAIN_AE:
    th_x = th.rand((1,3,120,200))
    model = SimpleDomainAutoEncoder(
      in_h=th_x.shape[2], in_w=th_x.shape[3],
      out_h=120, out_w=200,
      channels=th_x.shape[1],
      embed_size=256,
      domain_name='test'
    )
    print(model)
    th_y = model(th_x)
    print(th_y.shape)

    npars, nbytes = get_model_size(model)
    print('{:,} params, {:,} bytes'.format( npars,nbytes))

  if TEST_ADV_STN:
    mst = AdvancedSpatialTransformerNet(
      input_shape=(1,120,200),
      loc_layers=[(8,7), (10,5)],
      decode_act=True,
      decode_layers=[(16, 5), (32, 3)],
      decode_reshape=(24,94),
    )

    inp = th.rand((1, 1, 120, 200))
    print(mst)
    o = mst(inp)
    print(o[0].shape)
    print(o[1].shape)  