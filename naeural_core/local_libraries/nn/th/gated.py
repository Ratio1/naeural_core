import torch as th
from .utils import get_activation
  
class GateLayer(th.nn.Module):
  def __init__(self, input_dim, output_dim, bias=-2):
    super().__init__()
    self.gate_lin = th.nn.Linear(input_dim, output_dim)
    self.gate_act = th.nn.Sigmoid()
    if bias is not None:
      self.gate_lin.bias.data.fill_(bias) # negative fc => zero sigmoid => (1 - 0) * bypass
    return
    
  def forward(self, inputs, value1, value2):
    th_gate = self.gate_lin(inputs)
    th_gate = self.gate_act(th_gate)
    th_out = th_gate * value1 + (1 - th_gate) * value2
    return th_out
  
  
class MultiGatedDense(th.nn.Module):
  """
  TODO: 
    - experiment with various biases for gates
  """
  def __init__(self, input_dim, output_dim, activ):
    super().__init__()
    self.bypass = th.nn.Linear(input_dim, output_dim, bias=False)

    self.fc = th.nn.Linear(input_dim, output_dim)
    self.fc_act = get_activation(activ)
    
    self.bn_pre = th.nn.BatchNorm1d(output_dim)
    self.bn_post = th.nn.BatchNorm1d(output_dim)

    self.bn_pre_vs_post_gate = GateLayer(input_dim, output_dim, bias=0) 

    self.lnorm_post = th.nn.LayerNorm(output_dim)

    self.bn_vs_lnorm = GateLayer(input_dim, output_dim, bias=0)
    
    self.has_bn_gate = GateLayer(input_dim, output_dim, bias=-1)
    
    self.final_gate = GateLayer(input_dim, output_dim, bias=-2)
    return

  
  def forward(self, inputs):
    # bypass
    th_bypass = self.bypass(inputs)
    
    # FC unit
    th_fc = self.fc(inputs)
    th_fc_act = self.fc_act(th_fc)
    
    # apply post layer norm
    th_fc_act_lnorm = self.lnorm_post(th_fc_act)    

    # FC with pre activ bn
    th_bn_pre = self.fc_act(self.bn_pre(th_fc))
    # FC with post activ bn
    th_bn_post = self.bn_post(th_fc_act)
    
    # select between bn pre or post
    th_bn_out = self.bn_pre_vs_post_gate(inputs, th_bn_pre, th_bn_post)
    
    # select between bn or layer norm
    th_bn_vs_lnorm = self.bn_vs_lnorm(inputs, th_bn_out, th_fc_act_lnorm)
    
    # select between normed or FC-activ
    th_norm_vs_simple = self.has_bn_gate(inputs, th_bn_vs_lnorm, th_fc_act)
    
    # finally select between processed or bypass
    th_out = self.final_gate(inputs, th_norm_vs_simple, th_bypass)
  
    return th_out
    
    
  

class GatedDense(th.nn.Module):
  def __init__(self, input_dim, output_dim, activ, bn):
    super().__init__()
    self.bn = bn
    self.linearity = th.nn.Linear(input_dim, output_dim, bias=not bn)
    if self.bn == 'pre':
      self.pre_bn = th.nn.BatchNorm1d(output_dim)
    elif self.bn == 'post':
      self.post_bn = th.nn.BatchNorm1d(output_dim)
    elif self.bn == 'lnorm':
      self.post_lnorm = th.nn.LayerNorm(output_dim)
    self.activ = get_activation(activ)
    self.gate = GateLayer(input_dim, output_dim)
    self.bypass_layer = th.nn.Linear(input_dim, output_dim, bias=False)
  
  def forward(self, inputs):
    # the skip/residual
    th_bypass = self.bypass_layer(inputs)
    # the normal linear-bn-activ
    th_x = self.linearity(inputs)
    if self.bn == 'pre':
      th_x = self.pre_bn(th_x)
    th_x = self.activ(th_x)
    if self.bn == 'post':
      th_x = self.post_bn(th_x)
    elif self.bn == 'lnorm':
      th_x = self.post_lnorm(th_x)
    # the gate
    th_x = self.gate(inputs, th_x, th_bypass)
    return th_x
  
