from naeural_core.utils.sanchez_utils.Actor import *

BABY_HEDGE_STEPS_LIMIT = 25


class Hedgehog(Actor):
  def __init__(self, name='', color=None, actor_id=1,
               x=0, y=0, alpha=0, beta=0,
               speed=0, period=0, steps=0,
               life_state=0, baby_steps=0
               ):
    super(Hedgehog, self).__init__(
      name=name, color=color, actor_id=actor_id,
      x=x, y=y, alpha=alpha, beta=beta,
      speed=speed, period=period, steps=steps,
      life_state=life_state, baby_steps=baby_steps
    )

  def get_species(self):
    return TYPE_HEDGEHOG

  def _get_baby_steps_limit(self):
    return BABY_HEDGE_STEPS_LIMIT

  def make_baby(self, env, x, y):
    baby = Hedgehog(name=self.name + 'son', color=self.color, x=x, y=y)
    env.add_actor(baby)
    self.P(f'Baby {baby.get_data_string()} was born!')

  def manage_collision(self, actor, env, x, y):
    if type(actor) == type(self):
      if actor.life_state == BABY_STATE or actor.life_state == RETIRED_STATE:
        return

      self.make_baby(env, x, y)
      self._change_direction()

    elif actor.get_species() == TYPE_SNAKE:
      if actor.life_state == BABY_STATE or actor.life_state == ADULT_STATE:
        return

      actor.lose_life(env.get_current_map())
      self._change_direction()




