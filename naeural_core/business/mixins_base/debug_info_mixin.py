from collections import OrderedDict


class _DebugInfoMixin(object):
  def __init__(self):
    self._debug_info = []
    super(_DebugInfoMixin, self).__init__()
    return

  @staticmethod
  def _format_object_for_debug_info(obj, depth=0):
    if isinstance(obj, dict):
      if len(obj) == 0:
        msg = '{}'
      else:
        content = ' '.join(['{}:{}'.format(k, _DebugInfoMixin._format_object_for_debug_info(v, depth + 1))
                            for k, v in obj.items()])
        if depth > 0:
          msg = '{{{}}}'.format(content)
        else:
          msg = content
    elif isinstance(obj, list):
      # maybe compress the list?
      # but simply run the formatter on each of the objects contained
      if len(obj) == 0:
        msg = '[]'
      else:
        content = ' '.join([_DebugInfoMixin._format_object_for_debug_info(v, depth + 1) for v in obj])
        if depth > 0:
          msg = content
          # msg = '[{}]'.format(content) TODO: uncomment ar review time
        else:
          msg = content
    elif isinstance(obj, float):
      msg = f"{obj:.2f}"
    elif isinstance(obj, tuple):
      msg = ""
      for x in obj:
        msg += _DebugInfoMixin._format_object_for_debug_info(x, depth + 1)
    else:
      msg = str(obj)

    return msg

  def reset_debug_info(self):
    self._debug_info = []
    return

  def add_debug_info(self, value, key: str = None):
    """
    Add debug info to the witness. The information will be stored in a new line.

    Parameters
    ----------
    value : Any
        The info to be shown
    key : str
        The key that identifies this debug line
    """
    self._debug_info.append({
      'key': key,
      'value': value,
    })

    return

  def _draw_debug_info_on_witness(self, img_witness):
    img_sizes = [400, 700, 1000]
    font_sizes = {
      0: 0.2,
      1: 0.3,
      2: 0.4,
      3: 0.7
    }

    # This list will contain all the lines that will be drawn on the witness
    debug_info_lines = []

    # compute alerter data strings; TODO: check if default alerter is used
    any_alert = False
    for alerter_name in self.alerters_names:
      last_change_time = self.get_alerter(alerter_name).get_time_from_change()
      _is_default_alerter = (alerter_name == "default")
      _show_default_alerter = (_is_default_alerter and self.cfg_debug_show_default_alerter)
      _is_alert = self.alerter_is_alert(alerter_name)
      _not_changed_recently = last_change_time == -1 or last_change_time > self.cfg_debug_info_active_alerter_time
      _skip_alerter = not _show_default_alerter and (not _is_alert and _not_changed_recently)

      if _skip_alerter:
        # skip showing this alerter if no data has been added to it in the last
        # `DEBUG_INFO_ACTIVE_ALERTER_TIME` seconds
        continue

      is_alert = self.alerter_is_alert(alerter_name)
      txt_alert = '[{}] Alert: {}, {}'.format(
          alerter_name[:3], is_alert, self.get_alerter_status(alerter_name)
      )
      debug_info_lines.append(txt_alert)

      any_alert = any_alert or is_alert

    # encapsulating in try-catch so that we can dump the error and protect the OrderedDict
    # compute debug data strings
    for dct in self._debug_info:
      k, v = dct['key'], dct['value']

      txt_debug = ""
      if k is not None:
        txt_debug += '[{}] '.format(k)
      txt_debug += '{}'.format(self._format_object_for_debug_info(v))

      debug_info_lines.append(txt_debug)

    # draw debug info
    img_size = img_witness.shape[1]
    font_size = font_sizes[sum([img_size > s for s in img_sizes])]  # 0.7 if img_size > 1000 else 0.4
    font_thickness = 2 if img_size > 1000 else 1
    img_witness = self._painter.draw_text_outer_image(
        image=img_witness,
        text=debug_info_lines,
        font_size=font_size,
        thickness=font_thickness,
        color=self.consts.DEEP_RED if any_alert else self.consts.WHITE,
        location="top",
    )

    # reset debug info for next iteration
    self.reset_debug_info()

    return img_witness
