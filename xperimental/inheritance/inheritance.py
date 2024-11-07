
class _BaseMixin(object):
  def __init__(self):
    super(_BaseMixin).__init__()
    return

  def do_stuff(self, x):
    print('Base mixin implementation')
    print(x * 2)
    return


class Base(object):
  def __init__(self):
    super(Base).__init__()
    return


class Parent(
  Base, _BaseMixin
):
  def __init__(self):
    super(Parent).__init__()
    return

  def do_stuff_wrapper(self, x):
    print(f'Executing `do_stuff` method for {x}')
    self.do_stuff(x)
    return


class Child(Parent):
  def __init__(self):
    super(Child).__init__()
    return

  def do_stuff(self, x):
    print('Child implementation')
    print('i-auzi ba merge')
    return


if __name__ == '__main__':
  obj = Child()
  obj.do_stuff_wrapper(x=22)
