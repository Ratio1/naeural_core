import torch as th

class ConstrativeLoss(th.nn.Module):
  def __init__(self, margin=0.2):
    super(ConstrativeLoss, self).__init__()
    self.margin = margin
    
    
  def forward(self, dist, gold):
    th_d_sq = th.pow(dist, 2)
    th_d_sqm = th.pow(th.clamp(self.margin - dist, 0), 2)
    loss = (1 - gold) * th_d_sq + gold * th_d_sqm
    return loss.mean()
  
  def __repr__(self):
    s = self.__class__.__name__ + "(margin={})".format(
        self.margin,
        )
    return s  


class HuberLoss(th.nn.Module):
  def __init__(self, d=1.0):
    super(HuberLoss, self).__init__()
    self.d = d

  def forward(self, y_true, y_pred):
    th_res = y_pred - y_true
    cond1 = (th.abs(th_res) <= 0).float()# K.cast((K.abs(th_res) <= d), tf.float32)
    cond2 = (th.abs(th_res) > 0).float()#K.cast((K.abs(th_res) > d), tf.float32)
    th_batch_loss1 = cond1 * (0.5 * (th_res ** 2))
    th_batch_loss2 = cond2 * (self.d * (th.abs(th_res) - 0.5 * self.d))
    th_batch_loss = th_batch_loss1 + th_batch_loss2
    th_loss = th.mean(th_batch_loss, dim=1)
    return th_loss


class QuantileLoss(th.nn.Module):
  def __init__(self, Q):
    super(QuantileLoss, self).__init__()
    self.Q = Q

  def forward(self, inputs, targets):
    th_res = targets - inputs
    th_max = th.maximum(self.Q * th_res, (self.Q - 1) * th_res)
    th_loss = th.mean(th_max, dim=1)
    return th_loss


def quantile_loss_05():
  return QuantileLoss(Q=0.05)


def quantile_loss_95():
  return QuantileLoss(Q=0.95)

class FocalLossWithLogits_B(th.nn.Module):
  def __init__(self, alpha=0.3, gamma=2):
    super().__init__()
    assert alpha <= 0.9 and alpha >= 0.1
    self.alpha = alpha
    self.gamma = gamma

  def forward(self, inputs, targets):
    pos_alpha = self.alpha
    neg_alpha = 1 - self.alpha
    eps = 1e-14
    
    y_pred = th.sigmoid(inputs)
    
    pos_pt = th.where(targets==1 , y_pred , th.ones_like(y_pred)) # positive pt (fill all the 0 place in y_true with 1 so (1-pt)=0 and log(pt)=0.0) where pt is 1
    neg_pt = th.where(targets==0 , y_pred , th.zeros_like(y_pred)) # negative pt
    
    pos_pt = th.clamp(pos_pt, eps, 1 - eps)
    neg_pt = th.clamp(neg_pt, eps, 1 - eps)
    
    pos_modulating = th.pow(1-pos_pt, self.gamma) # compute postive modulating factor for correct classification the value approaches to zero
    neg_modulating = th.pow(neg_pt, self.gamma) # compute negative modulating factor
    
    
    pos = - pos_alpha * pos_modulating * th.log(pos_pt) #pos part
    neg = - neg_alpha * neg_modulating * th.log(1 - neg_pt) # neg part
    
    loss = pos + neg  # this is final loss to be returned with some reduction
    
    return th.mean(loss)
      
  def __repr__(self):
    s = self.__class__.__name__ + "(alpha={}, gamma={})".format(
        self.alpha,
        self.gamma,
        )
    return s
  
class FocalLossWithLogits_A(th.nn.Module):
  def __init__(self, alpha=4, gamma=2):
    super().__init__()
    self.alpha = alpha
    self.gamma = gamma

  def forward(self, inputs, targets):
    BCE_loss = th.nn.functional.binary_cross_entropy_with_logits(
        inputs, 
        targets, 
        reduction='none',
        )

    pt = th.exp(-BCE_loss)
    F_loss = self.alpha * th.pow(1 - pt, self.gamma) * BCE_loss
    return th.mean(F_loss)
      
  def __repr__(self):
    s = self.__class__.__name__ + "(alpha={}, gamma={})".format(
        self.alpha,
        self.gamma,
        )
    return s
  