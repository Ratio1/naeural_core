from naeural_core import constants as ct
import math
import random


SIZE = 3
BABY_STATE = 0
ADULT_STATE = 1
RETIRED_STATE = 2
BABY_STEPS_LIMIT = 5
BACK_TRIES_LIMIT = 1000
LIFE_STATES = ['BABY', 'ADULT', 'RETIRED']
EPSILON = 0.0001

TYPE_SNAKE = 'SNAKE'
TYPE_HEDGEHOG = 'HEDGEHOG'


class Actor:
  def __init__(self, name='', color=None, actor_id=1,
               x=0, y=0, alpha=0, beta=0,
               speed=0, period=0, steps=0,
               life_state=ADULT_STATE, baby_steps=0):
    self.name = name
    self.color = color if color is not None else ct.LIGHT_BLUE
    actor_id = int(actor_id)
    assert actor_id > 0, "actor_id should be greater than 0"
    self.id = int(actor_id)
    self.x = x
    self.y = y
    self.alpha = alpha
    self.beta = beta
    self.speed = speed
    self.period = period
    self.steps = steps
    self.life_state = life_state
    self.baby_steps = baby_steps

  def get_life_state_name(self):
    return LIFE_STATES[self.life_state]

  def get_data_string(self):
    return f'{self.id}, {self.name}, {self.get_life_state_name()}'

  def _change_direction(self, h=0, w=0):
    # alpha si beta sunt de fapt un R * sin, respectiv R * cos
    # deci o rotatie nu poate sa fie chiar solutia ideala, pt ca trebuie sa luam in calcul
    # unghiul curent.
    rad = random.uniform(0, 2 * math.pi)
    s, c = math.sin(rad), math.cos(rad)

    radius = math.sqrt(self.alpha * self.alpha + self.beta * self.beta)

    alpha = s * radius
    beta = c * radius

    if w != 0:
      beta = w * abs(beta)
    if h != 0:
      alpha = h * abs(alpha)

    self.alpha, self.beta = alpha, beta

  def change_direction(self):
    self._change_direction()
    self.steps = 0

  def _future_position(self, speed=None):
    if speed is None:
      speed = self.speed
    x = self.x + speed * self.alpha
    y = self.y + speed * self.beta

    return x, y

  def _step_forward(self, speed=None):
    self.x, self.y = self._future_position(speed)

  def _out_of_map(self, h, w, x=None, y=None):
    if x is None:
      x, y = self.x, self.y

    if x < 0 or y < 0:
      return True

    if h is not None and x >= h - EPSILON:
      return True
    if w is not None and y >= w - EPSILON:
      return True

    return False

  def _get_baby_steps_limit(self):
    return BABY_STEPS_LIMIT

  def make_adult(self):
    self.life_state = ADULT_STATE

    if self.speed == 0:
      self.speed = 1

    if self.alpha == 0 and self.beta == 0:
      self.alpha, self.beta = 0, 1

  def retire(self):
    self.life_state = RETIRED_STATE
    self.speed = 0
    self.alpha = 0
    self.beta = 0
    self.color = (100, 100, 100)

  def _check_baby_state(self):
    if self.life_state == BABY_STATE:
      self.baby_steps += 1
      if self.baby_steps > BABY_STEPS_LIMIT:
        self.make_adult()

      return True
    else:
      return False

  def check_same_direction(self):
    if self.period > 0 and self.steps % self.period < 1:
      self.change_direction()

  def still_in_map(self, x, y, h, w, speed=None):
    if not self._out_of_map(h, w, x, y):
      return x, y
    else:
      if speed is None:
        speed = self.speed
      tried = 0
      while self._out_of_map(h, w, x, y) and tried < BACK_TRIES_LIMIT:
        ch, cw = 0, 0
        if x < 0:
          ch = 1
        if h is not None and x >= h - EPSILON:
          ch = -1

        if y < 0:
          cw = 1
        if w is not None and y >= w - EPSILON:
          cw = -1

        self._change_direction(h=ch, w=cw)

        x, y = self._future_position(speed)
        tried += 1

      if tried < BACK_TRIES_LIMIT:
        self.steps = 0
        return x, y
      else:
        self.retire()
        return None, None

  def manage_collision(self, actor, env, x, y):
    self.change_direction()
    actor.change_direction()

  def forward(self, h=None, w=None, env=None):
    if self._check_baby_state():
      return

    if self.life_state == RETIRED_STATE:
      return

    self.check_same_direction()

    x, y = self._future_position()

    x, y = self.still_in_map(x, y, h, w)

    if x is not None:
      if env is not None:
        p_x, p_y = self.matrix_position(h, w, x, y)
        mat = env.get_current_map()

        current_value = mat[p_x, p_y]
        if current_value != 0:
          if current_value != self.id:
            if current_value > 0:
              # another actor
              actor = env.get_actor(current_value)
              self.manage_collision(actor, env, p_x, p_y)
            else:
              self.change_direction()
              # self.retire()
      # endif

      if self.life_state != RETIRED_STATE:
        self._step_forward()
        self.steps += 1

  def matrix_position(self, h=None, w=None, x=None, y=None):
    if x is None:
      x, y = self.x, self.y

    ans_x, ans_y = max(0, x), max(0, y)

    if w is not None:
      ans_y = min(ans_y, w - 1)
    if h is not None:
      ans_x = min(ans_x, h - 1)

    return math.floor(ans_x), math.floor(ans_y)

  def get_coords(self, h=None, w=None):
    x, y = self.matrix_position()
    xmin, xmax = x - SIZE, x + SIZE
    ymin, ymax = y - SIZE, y + SIZE
    xmin, ymin = max(xmin, 0), max(ymin, 0)

    if w is not None:
      ymax = min(ymax, w)
    if h is not None:
      xmax = min(xmax, h)

    return xmin, xmax, ymin, ymax

  def get_points(self, h=None, w=None):
    return [self.matrix_position(h, w)]

  def get_color(self):
    return self.color

  def get_id(self):
    return self.id

  def _head_collision(self, other):
    x1, y1 = self.matrix_position()
    x2, y2 = other.matrix_position()

    return x1 == x2 and y1 == y2

  def check_collision(self, other):
    return self._head_collision(other)

