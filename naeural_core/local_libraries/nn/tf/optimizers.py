VER = '0.0.1'

from tensorflow.python.framework import ops
from tensorflow.python.ops import state_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.framework import constant_op
import tensorflow as tf


class COCOB(tf.keras.optimizers.Optimizer):
  def __init__(self, alpha=100, use_locking=False, name='COCOB', **kwargs):
    '''
    constructs a new COCOB optimizer - copyright 2017 Francesco Orabona
    https://github.com/bremen79/cocob
    '''
    super(COCOB, self).__init__(name, **kwargs)
    self._alpha = alpha
    self._use_locking = use_locking

  def _create_slots(self, var_list):
    for v in var_list:
      with ops.colocate_with(v):
        gradients_sum = constant_op.constant(0,
                                             shape=v.get_shape(),
                                             dtype=v.dtype.base_dtype)
        grad_norm_sum = constant_op.constant(0,
                                             shape=v.get_shape(),
                                             dtype=v.dtype.base_dtype)
        L = constant_op.constant(1e-8, shape=v.get_shape(), dtype=v.dtype.base_dtype)
        tilde_w = constant_op.constant(0.0, shape=v.get_shape(), dtype=v.dtype.base_dtype)
        reward = constant_op.constant(0.0, shape=v.get_shape(), dtype=v.dtype.base_dtype)

      self.add_slot(v, "L", initializer=L)
      self.add_slot(v, "grad_norm_sum", initializer=grad_norm_sum)
      self.add_slot(v, "gradients_sum", initializer=gradients_sum)
      self.add_slot(v, "tilde_w", initializer=tilde_w)
      self.add_slot(v, "reward", initializer=reward)

  def _apply_dense(self, grad, var):
    gradients_sum = self.get_slot(var, "gradients_sum")
    grad_norm_sum = self.get_slot(var, "grad_norm_sum")
    tilde_w = self.get_slot(var, "tilde_w")
    L = self.get_slot(var, "L")
    reward = self.get_slot(var, "reward")

    L_update = tf.maximum(L, tf.abs(grad))
    gradients_sum_update = gradients_sum + grad
    grad_norm_sum_update = grad_norm_sum + tf.abs(grad)
    reward_update = tf.maximum(reward - grad * tilde_w, 0)
    new_w = -gradients_sum_update / (
              L_update * (tf.maximum(grad_norm_sum_update + L_update, self._alpha * L_update))) * (
                      reward_update + L_update)
    var_update = var - tilde_w + new_w
    tilde_w_update = new_w

    gradients_sum_update_op = state_ops.assign(gradients_sum, gradients_sum_update)
    grad_norm_sum_update_op = state_ops.assign(grad_norm_sum, grad_norm_sum_update)
    var_update_op = state_ops.assign(var, var_update)
    tilde_w_update_op = state_ops.assign(tilde_w, tilde_w_update)
    L_update_op = state_ops.assign(L, L_update)
    reward_update_op = state_ops.assign(reward, reward_update)

    return control_flow_ops.group(*[gradients_sum_update_op,
                                    var_update_op,
                                    grad_norm_sum_update_op,
                                    tilde_w_update_op,
                                    reward_update_op,
                                    L_update_op])

  def _apply_sparse(self, grad, var):
    return self._apply_dense(grad, var)

  def _resource_apply_dense(self, grad, handle):
    return self._apply_dense(grad, handle)

  def _resource_apply_sparse(self, grad, handle, indices, apply_state=None):
    raise NotImplementedError()

  def get_config(self):
    config = {
      'alpha': self._alpha,
      'use_locking': self._use_locking
    }
    base_config = super().get_config()
    cfg = dict(list(base_config.items()) + list(config.items()))
    return cfg

  @classmethod
  def from_config(cls, config, custom_objects=None):
    print("\x1b[1;33mLoading COCOB optimizer\x1b[0m")
    return cls(**config)