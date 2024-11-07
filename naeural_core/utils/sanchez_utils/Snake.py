from collections import deque
from math import sin

from naeural_core.utils.sanchez_utils.Actor import *

BABY_SNAKE_STEPS_LIMIT = 5
SELF_EATING = False


class Snake(Actor):
  def __init__(self, name='', color=None, actor_id=1,
               x=0, y=0, alpha=0, beta=0,
               speed=0, period=0, steps=0,
               life_state=0, length=1, queue=None,
               extra_lifes=0, baby_steps=0
               ):
    super(Snake, self).__init__(
      name=name, color=color, actor_id=actor_id,
      x=x, y=y, alpha=alpha, beta=beta,
      speed=speed, period=period, steps=steps,
      life_state=life_state, baby_steps=baby_steps
    )

    self.length = length
    self._queue = deque([(self.x, self.y)]) if queue is None else queue
    self.extra_lifes = extra_lifes
    self.to_move = 0

  def get_species(self):
    return TYPE_SNAKE

  def get_data_string(self):
    return super(Snake, self).get_data_string() + f', {self.length}'

  def _get_baby_steps_limit(self):
    return BABY_SNAKE_STEPS_LIMIT

  def retire(self):
    super(Snake, self).retire()
    self._queue = None
    self.length = 1
    self.to_move = 0

  def clear_map(self, mat):
    for (x, y) in self._queue:
      mat[x, y] = 0

  def make_baby(self, env, x, y):
    baby = Snake(name=self.name + 'son', color=self.color, x=x, y=y)
    env.add_actor(baby)
    print(f'Baby {baby.get_data_string()} was born!')

  def lose_life(self, mat):
    if self.extra_lifes > 0:
      self.extra_lifes -= 1
    else:
      self.clear_map(mat)
      self.retire()

  def manage_collision(self, actor, env, x, y):
    if type(actor) == type(self):
      if actor.life_state == BABY_STATE or actor.life_state == RETIRED_STATE:
        return
      if self.length >= actor.length:
        actor.clear_map(env.get_current_map())
        actor.retire()
        self.length += 1
      elif self.length < actor.length / 2:
        self.clear_map(env.get_current_map())
        self.retire()
        actor.length += 1
      else:
        self.make_baby(env, x, y)
        self._change_direction()
    elif actor.get_species() == TYPE_HEDGEHOG:
      if actor.life_state == ADULT_STATE:
        return
      if actor.life_state == BABY_STATE:
        actor.retire()
        self.extra_lifes += 1
        return

      self.lose_life(env.get_current_map())
      self._change_direction()

  def get_points(self, h=None, w=None):
    if self._queue is not None and len(self._queue) > 0:
      return list(self._queue)
    return super(Snake, self).get_points(h, w)

  def _future_position(self, speed=None):
    if not hasattr(self, 'rounds_lived'):
      self.rounds_lived = 0
    else:
      self.rounds_lived += 1
      
    if speed is None:
      speed = self.speed
    x, y = super()._future_position(speed)
    
    # now jiggle
    x += self.beta * sin(0.4 * (speed * self.rounds_lived))
    y -= self.alpha * sin(0.4 * (speed * self.rounds_lived))
    return x, y

  def forward(self, h=None, w=None, env=None):
    if self._check_baby_state():
      return

    if self.life_state == RETIRED_STATE:
      return

    self.to_move = self.to_move + self.speed
    steps_to_make = int(self.to_move)
    self.to_move = self.to_move - steps_to_make

    points_to_free = len(self._queue) + steps_to_make - self.length
    if points_to_free > len(self._queue):
      points_to_free = points_to_free - len(self._queue)
      removed_points = [self._queue.popleft() for _ in range(len(self._queue))]
    else:
      points_to_free = max(points_to_free, 0)
      removed_points = [self._queue.popleft() for _ in range(int(points_to_free))]
      points_to_free = 0

    if env is not None:
      mat = env.get_current_map()
      for (i, j) in removed_points:
        mat[i, j] = 0

    added_points = []
    for _ in range(steps_to_make):
      if points_to_free > 0:
        if len(self._queue) > 0:
          (i, j) = self._queue.popleft()
          removed_points.append((i, j))
          points_to_free -= 1
          if env is not None:
            env.get_current_map()[i, j] = 0

      x, y = self._future_position(speed=1)
      x, y = self.still_in_map(x, y, h, w, 1)

      if x is None:
        return

      if env is not None:
        mat = env.get_current_map()
        p_x, p_y = self.matrix_position(h, w, x, y)

        current_value = mat[p_x, p_y]
        if current_value != 0:
          if current_value > 0:
            # actor here
            if current_value == self.id:
              if SELF_EATING:
                # self intersection
                self.clear_map(mat)
                self.retire()
                return
            else:
              actor = env.get_actor(current_value)
              self.manage_collision(actor, env, p_x, p_y)
          else:
            # stone here
            self.clear_map(mat)
            self.retire()

        if self.life_state == RETIRED_STATE:
          return
      # endif
      self._step_forward(1)
      added_points.append(self.matrix_position(h, w))
    # endfor

    for point in added_points[points_to_free:]:
      self._queue.append(point)

    if env is not None:
      mat = env.get_current_map()
      for point in added_points[points_to_free:]:
        mat[point] = self.id










