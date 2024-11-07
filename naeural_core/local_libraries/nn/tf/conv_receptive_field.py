import math
import tensorflow as tf

def calc_field(kernel, stride, padding,
              n_feat_prev, j_dist_prev, recept_prev, start_prev):
  """
  k_curr: kernel size
  s_curr: stride
  p_curr: padding (if padding is uneven, right padding will higher than left padding; "SAME" option in tensorflow)
  
  
  n_feat_prev: number of feature (data layer has n_1 = imagesize )
  # - j_i: distance (projected to image pixel distance) between center of two adjacent features
  # - r_i: receptive field of a feature in layer i
  # - start_i: position of the first feature's receptive field in layer i (idx start from 0, negative means the center fall into padding)
  """
  
  n_out = math.floor((n_feat_prev - kernel + 2 * padding) / stride) + 1
  actualP = (n_out - 1) * stride - n_feat_prev + kernel 
  pR = math.ceil(actualP/2)
  pL = math.floor(actualP/2)
  
  j_out = j_dist_prev * stride
  r_out = recept_prev + (kernel - 1) * j_dist_prev
  start_out = start_prev + ((kernel - 1)/2 - pL) * j_dist_prev
  return n_out, j_out, r_out, start_out


def analyze_model(model):
  n_p = int(model.inputs[0].shape[-2])
  j_p = 1
  r_p = 1
  s_p = 0.5
  inp_feats = n_p
  for i in range(1, len(model.layers)):
    layer = model.layers[i]
    ltype = str(type(layer)).lower()
    if ("conv" in ltype) or ("pool" in ltype):      
      kernel = layer.kernel_size[0] if "conv" in ltype else layer.pool_size[0]
      stride = layer.strides[0]
      padding = layer.padding
      if padding == 'vald':
        padding = 0
        out_feats = math.ceil((inp_feats - kernel) / stride + 1)
      elif padding == 'same':
        out_feats = math.ceil(float(inp_feats) / float(stride))
        padding = max(0, (out_feats - 1) * stride + kernel - inp_feats) // 2
      else:
        raise ValueError("Padding {} not supported".format(padding))
      n_p_i = n_p
      j_p_i = j_p
      r_p_i = r_p
      s_p_i = s_p
      n_p, j_p, r_p, s_p = calc_field(kernel, stride, padding, n_p, j_p, r_p, s_p)
      inp_feats = out_feats
      print("Layer {:>3}: inp: {:>4}  out: {:>4}  pad: {}  recv field {:>3}".format(i
            , inp_feats, out_feats, padding, r_p))
      print("  prev_feats: {}".format(n_p_i))
      print("  prev_j_dst: {}".format(j_p_i))
      print("  prev_rcv_s: {}".format(r_p_i))
      print("  prev_start: {}".format(s_p_i))
      print("  outp_feats: {}".format(n_p))
      print("  outp_j_dst: {}".format(j_p))
      print("  outp_rcv_s: {}".format(r_p))
      print("  outp_start: {}".format(s_p))
  return r_p


def define_PatchGAN_discriminator(image_shape):
  """
  Defines a PatchGAN discriminator: 3x K=4 S=2 convs + 2x K=4 S=1 convs
  
  def __init__(self, input_nc, ndf=64, n_layers=3, norm_layer=nn.BatchNorm2d):
      '''Construct a PatchGAN discriminator
      Parameters:
          input_nc (int)  -- the number of channels in input images
          ndf (int)       -- the number of filters in the last conv layer
          n_layers (int)  -- the number of conv layers in the discriminator
          norm_layer      -- normalization layer
      '''
      super(NLayerDiscriminator, self).__init__()
      if type(norm_layer) == functools.partial:  # no need to use bias as BatchNorm2d has affine parameters
          use_bias = norm_layer.func == nn.InstanceNorm2d
      else:
          use_bias = norm_layer == nn.InstanceNorm2d
  
      kw = 4
      padw = 1
      sequence = [nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw), nn.LeakyReLU(0.2, True)]
      nf_mult = 1
      nf_mult_prev = 1
      for n in range(1, n_layers):  # gradually increase the number of filters
          nf_mult_prev = nf_mult
          nf_mult = min(2 ** n, 8)
          sequence += [
              nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult, kernel_size=kw, stride=2, padding=padw, bias=use_bias),
              norm_layer(ndf * nf_mult),
              nn.LeakyReLU(0.2, True)
          ]
  
      nf_mult_prev = nf_mult
      nf_mult = min(2 ** n_layers, 8)
      sequence += [
          nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult, kernel_size=kw, stride=1, padding=padw, bias=use_bias),
          norm_layer(ndf * nf_mult),
          nn.LeakyReLU(0.2, True)
      ]
  
      sequence += [nn.Conv2d(ndf * nf_mult, 1, kernel_size=kw, stride=1, padding=padw)]  # output 1 channel prediction map
      self.model = nn.Sequential(*sequence)	
  """
  
  # weight initialization
  init = tf.keras.initializers.RandomNormal(stddev=0.02)
	# source image input
  in_image = tf.keras.layers.Input(shape=image_shape)
  # C64
  d = tf.keras.layers.Conv2D(64, (4,4), strides=(2,2), padding='same', kernel_initializer=init)(in_image)
  d = tf.keras.layers.LeakyReLU(alpha=0.2)(d)
  # C128
  d = tf.keras.layers.Conv2D(128, (4,4), strides=(2,2), padding='same', kernel_initializer=init)(d)
  d = tf.keras.layers.BatchNormalization(axis=-1)(d)  # InstanceNormalization
  d = tf.keras.layers.LeakyReLU(alpha=0.2)(d)
  # C256
  d = tf.keras.layers.Conv2D(256, (4,4), strides=(2,2), padding='same', kernel_initializer=init)(d)
  d = tf.keras.layers.BatchNormalization(axis=-1)(d)  # InstanceNormalization
  d = tf.keras.layers.LeakyReLU(alpha=0.2)(d)
  # C512
  #d = tf.keras.layers.Conv2D(512, (4,4), strides=(2,2), padding='same', kernel_initializer=init)(d)
  #d = tf.keras.layers.BatchNormalization(axis=-1)(d)  # InstanceNormalization
  #d = tf.keras.layers.LeakyReLU(alpha=0.2)(d)
  # second last output layer
  d = tf.keras.layers.Conv2D(512, (4,4), padding='same', kernel_initializer=init)(d)
  d = tf.keras.layers.BatchNormalization(axis=-1)(d)  # InstanceNormalization
  d = tf.keras.layers.LeakyReLU(alpha=0.2)(d)
  # patch output
  patch_out = tf.keras.layers.Conv2D(1, (4,4), padding='same', kernel_initializer=init)(d)
  # define model
  model = tf.keras.models.Model(in_image, patch_out)
  # compile model
  model.compile(loss='mse', optimizer=tf.keras.optimizers.Adam(lr=0.0002, beta_1=0.5), loss_weights=[0.5])
  return model    

def conv1_net(shape=(1000,1)):
  in_seq = tf.keras.layers.Input(shape)
  x = in_seq
  x = tf.keras.layers.Conv1D(32, kernel_size=3, strides=1, padding='same')(x)
  x = tf.keras.layers.Conv1D(32, kernel_size=3, strides=1, padding='same')(x)
  x = tf.keras.layers.Conv1D(32, kernel_size=3, strides=1, padding='same')(x)
  model = tf.keras.models.Model(in_seq, x)
  return model
    
if __name__ == '__main__':
  #img_shape = (500,500,3)
  # m = define_PatchGAN_discriminator(img_shape)
  inp_seq = (1000,1)
  m = conv1_net(inp_seq)
  analyze_model(m)