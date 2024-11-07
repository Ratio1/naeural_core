import numpy as np

from naeural_core.utils.sanchez_utils.Actor import *
from naeural_core.utils.sanchez_utils import utils


class Environment:
  def __init__(self, name='', h=90, w=170, actors=None, seconds_per_turn=1, background=None):
    self._last_map = None
    self.name = name
    self.h = h
    self.w = w
    self.background = background if background is not None else ct.WHITE
    self.seconds_per_turn = seconds_per_turn
    self.running = False
    self.turns_passed = 0

    self._map = np.zeros((h, w), np.int32)
    self._last_map = self._map
    # self._frame = np.full((h, w, 3), self.background, np.uint8)

    self._stones = []
    self.actors = []
    if actors is not None:
      for actor in actors:
        self.add_actor(actor)

    self.img = None

  def add_stone(self, x, y):
    if 0 < x < self.h and 0 < y < self.w:
      if self._map[x, y] > 0:
        actor = self.actors[self._map[x, y] - 1]
        if actor.get_species() == TYPE_SNAKE:
          actor.clear_map(self._map)
        actor.retire()
      self._map[x, y] = -1
      self._stones.append((x, y))
    return

  def _remove_stone(self, x, y):
    if self._in_map(x, y):
      if self._map[x, y] == -1:
        self._map[x, y] = 0
        stones_set = set(self._stones)
        if (x, y) in stones_set:
          stones_set.remove((x, y))
          self._stones = list(stones_set)
    return

  def _reset_stones(self):
    for (x, y) in self._stones:
      self._remove_stone(x, y)
    return

  def update_stones(self, stones):
    stones_set = set(self._stones)
    for (x, y) in stones:
      if (x, y) not in stones_set:
        self.add_stone(x, y)
    return

  def get_stones(self):
    return self._stones

  def add_actor(self, actor):
    x, y = actor.matrix_position(self.h, self.w)
    if self._map[x, y] < 0:
      # a stone already there
      return

    self.actors.append(actor)
    actor_id = len(self.actors)
    self.actors[len(self.actors) - 1].id = actor_id
    self._map[x, y] = actor_id

  def get_actor(self, idx=1):
    idx = idx - 1
    if 0 <= idx < len(self.actors):
      return self.actors[idx]
    return None

  def get_actors_data(self):
    # used for displaying actors names
    datas = []
    for actor in self.actors:
      text = actor.get_data_string()
      species = actor.__class__.__name__.lower()
      x, y = actor.matrix_position(self.h, self.w)
      a = actor.alpha
      b = actor.beta
      points = actor.get_points()
      id = actor.get_id()
      datas.append((text, species, x, y, a, b, points, id))

    return datas

  def get_env_colors(self):
    # color[0] will be the color of the rocks
    # color[1] will be the color of the background
    # color[i + 1] will be the color of the actor
    # to get the colored map use colors[env.get_map() + 1]
    colors = np.full((len(self.actors) + 2, 3), self.background, np.uint8)
    colors[0] = utils.negate_color(self.background)

    for actor in self.actors:
      colors[actor.get_id() + 1] = actor.get_color()

    return colors

  def _check_collisions(self):
    N = len(self.actors)
    for i in range(N):
      for j in range(i + 1, N):
        if self.actors[i].check_collision(self.actors[j]):
          self.actors[i].change_direction()
          self.actors[j].change_direction()

  def _reset_map(self):
    for actor in self.actors:
      # xmin, xmax, ymin, ymax = actor.get_coords()
      # self._map[xmin: xmax, ymin: ymax] = 0
      self._map[actor.matrix_position(self.h, self.w)] = 0

  def _in_map(self, x, y):
    return -1 < x < self.h and -1 < y < self.w

  def _map_position(self, point):
    x, y = point
    x, y = max(x, 0), max(y, 0)
    h, w = self._map.shape
    x, y = min(x, h - 1), min(y, w - 1)
    point = x, y
    return point

  def _update_map(self):
    for actor in self.actors:
      points = actor.get_points()
      for point in points:
        self._map[self._map_position(point)] = actor.get_id()

    self._last_map = self._map

  def get_current_map(self):
    return self._map

  def get_map(self):
    return self._last_map

  def get_frame(self, scale_x=1, scale_y=1):
    big_map = self._map.repeat([scale_x] * self.w, axis=1)
    big_map = big_map.repeat([scale_y] * self.h, axis=0)

    colors = np.full((len(self.actors) + 2, 3), self.background, np.uint8)
    colors[0] = utils.negate_color(self.background)

    for actor in self.actors:
      colors[actor.id + 1] = actor.color

    return colors[big_map + 1]

  def stop(self, verbose=False):
    self.running = False
    if verbose:
      print('Env should stop')

  def dead_environment(self):
    for actor in self.actors:
      if actor.life_state != RETIRED_STATE:
        return False
    return True

  def take_turn(self, verbose=False):
    if verbose:
      print('Iteration on Environment loop')
    self._reset_map()

    for actor in self.actors:
      actor.forward(self.h, self.w, self)

    # self._check_collisions()

    self._update_map()
    self.turns_passed += 1
    # time.sleep(self.seconds_per_turn)
    if verbose:
      print(f'Done turn #{self.turns_passed}')

  def start(self, turn_limit=None, verbose=False, auto_turn=False):
    self._update_map()
    if verbose:
      print(f'Turn limit is {turn_limit}')

    self.running = True

    if auto_turn:
      while self.running and (turn_limit is None or self.turns_passed < turn_limit):
        self.take_turn(verbose)

      if verbose:
        print(f'Environment {self.name} stopped after {self.turns_passed} turns.')

    # endif
    return









