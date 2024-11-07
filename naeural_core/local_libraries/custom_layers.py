# simple wrappers
from .nn.tf.layers import OneHotLayer, SqueezeLayer, SliceAxis1Layer, SliceLayer,\
  SinCosEncodingLayer, RepeatElements, SplitLayer

# advanced layers
from .nn.tf import GatedDense, MultiGatedUnit, SmartSeparableConv2D

# losses
from .nn.tf.losses import (
  focal_loss_softmax, focal_loss_sigmoid, K_huber_loss,
  huber_loss, quantile_loss, quantile_loss_05, quantile_loss_95
)

#optimizers
from .nn.tf.optimizers import COCOB

CUSTOM_LAYERS = {
      "OneHotLayer"         : OneHotLayer,
      "SinCosEncodingLayer" : SinCosEncodingLayer,
      "RepeatElements"      : RepeatElements,
      "SplitLayer"          : SplitLayer,
      "SqueezeLayer"        : SqueezeLayer, 
      "GatedDense"          : GatedDense,     
      "SliceAxis1Layer"     : SliceAxis1Layer,
      "SliceLayer"          : SliceLayer,
      "huber_loss"          : huber_loss,
      "K_huber_loss"        : K_huber_loss,
      "quantile_loss"       : quantile_loss,
      "quantile_loss_05"    : quantile_loss_05,
      "quantile_loss_95"    : quantile_loss_95,
      "focal_loss_softmax"  : focal_loss_softmax,
      "focal_loss_sigmoid"  : focal_loss_sigmoid,
      "MultiGatedUnit"      : MultiGatedUnit,
      "SmartSeparableConv2D": SmartSeparableConv2D,

      "COCOB"               : COCOB
    }
