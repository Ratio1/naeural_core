IDX_TO_STR = {
  0: "nose",
  1: "left eye",
  2: "right eye",
  3: "left ear",
  4: "right ear",
  5: "left shoulder",
  6: "right shoulder",
  7: "left elbow",
  8: "right elbow",
  9: "left hand",
  10: "right hand",
  11: "left hip",
  12: "right hip",
  13: "left knee",
  14: "right knee",
  15: "left foot",
  16: "right foot"
}

STR_TO_IDX = {v: k for k, v in IDX_TO_STR.items()}


class _PoseAPIMixin(object):
  def __init__(self, *args, **kwargs):
    super(_PoseAPIMixin, self).__init__(*args, **kwargs)
    self.create_poseapi_keypoints_getters()
    return

  def poseapi_extract_coords_and_scores(self, tlbr, kpt_with_conf, to_flip=False, inverse_keypoint_coords=False):
    """
    Extracts the coordinates and scores of the keypoints from the detection along with
    the height and width of the specified person.
    Parameters
    ----------
    tlbr : np.ndarray or list, [top, left, bottom, right] coordinates of the bounding box
    kpt_with_conf : np.ndarray (N, 3) coordinates  and scores of the keypoints in the format [x, y, score]
    to_flip : bool, whether to flip the keypoints
    inverse_keypoint_coords : bool,
      if True the first value of the coordinates will be scaled by the width and the second by the height
      if False the first value of the coordinates will be scaled by the height and the second by the width

    Returns
    -------
    keypoint_coords : np.ndarray (N, 2) coordinates of the keypoints
    keypoint_scores : np.ndarray (N,) scores of the keypoints
    height : int, height of the person
    width : int, width of the person
    """
    keypoint_scores = kpt_with_conf[:, 2]
    # deepcopy of the keypoints detection in order to not alterate
    # the values when dealing with multiple plugins that use the same model
    keypoint_coords = self.deepcopy(kpt_with_conf[:, :2])
    keypoint_coords, height, width = self.gmt.convert_detections(
      tlbr=tlbr, keypoint_coords=keypoint_coords, to_flip=to_flip,
      inverse_keypoint_coords=inverse_keypoint_coords
    )

    return keypoint_coords, keypoint_scores, height, width

  def poseapi_keypoints_dict(self):
    """
    Returns a dictionary with the keypoints and their indexes
    """
    return IDX_TO_STR

  def poseapi_keypoint_name(self, idx):
    """
    Returns the name of the keypoint
    """
    return IDX_TO_STR[idx]

  def poseapi_keypoint_indexes(self):
    """
    Returns a dictionary with the indexes of the keypoints and their names
    """
    return STR_TO_IDX

  def create_poseapi_keypoints_getters(self):
    """
    Creates the getters for the keypoints
    To access the index of a keypoint use the following:
    self.poseapi_get_nose_idx # returns 0
    """
    for k, v in IDX_TO_STR.items():
      setattr(self, f"poseapi_get_{v.replace(' ', '_')}_idx", k)
    return

  def poseapi_is_left_sided(self, idx):
    """
    Returns whether the keypoint is left sided
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is left sided
    """
    return idx % 2 == 1

  def poseapi_is_right_sided(self, idx):
    """
    Returns whether the keypoint is right sided
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is right sided
    """
    return idx > 0 and idx % 2 == 0

  def poseapi_is_face_keypoint(self, idx):
    """
    Returns whether the keypoint is a face keypoint
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is a face keypoint
    """
    return idx < 5

  def poseapi_is_upper_body_keypoint(self, idx):
    """
    Returns whether the keypoint is an upper body keypoint
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is an upper body keypoint
    """
    return 5 <= idx <= 10

  def poseapi_is_lower_body_keypoint(self, idx):
    """
    Returns whether the keypoint is a lower body keypoint
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is a lower body keypoint
    """
    return 11 <= idx <= 16

  def poseapi_is_arm_keypoint(self, idx):
    """
    Returns whether the keypoint is an arm keypoint
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is an arm keypoint
    """
    return 5 <= idx <= 10

  def poseapi_is_leg_keypoint(self, idx):
    """
    Returns whether the keypoint is a leg keypoint
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is a leg keypoint
    """
    return 11 <= idx <= 16

  def poseapi_is_extremity_keypoint(self, idx):
    """
    Returns whether the keypoint is an extremity keypoint(a hand or a foot).
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is an extremity keypoint
    """
    return idx in [9, 10, 15, 16]

  def poseapi_get_color(self, idx):
    """
    Returns the color of the keypoint
    COLORCODE:
    - face: purple
    - arms: white for left and black for right
    - legs: light blue for left and dark blue for right
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    tuple : color of the keypoint
    """
    if self.poseapi_is_arm_keypoint(idx):
      if self.poseapi_is_left_sided(idx):
        return self.consts.WHITE
      else:
        return self.consts.BLACK
    elif self.poseapi_is_leg_keypoint(idx):
      if self.poseapi_is_left_sided(idx):
        return self.consts.LIGHT_BLUE
      else:
        return self.consts.DARK_BLUE
    return self.consts.PURPLE

  def poseapi_get_keypoint_color(self, idx):
    """
    Alias for poseapi_get_color
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    tuple : color of the keypoint
    """
    return self.poseapi_get_color(idx)

  def poseapi_is_insertion_keypoint(self, idx):
    """
    Returns whether the keypoint is an insertion keypoint(hip or shoulder).
    Parameters
    ----------
    idx : int, index of the keypoint

    Returns
    -------
    bool : whether the keypoint is an insertion keypoint
    """
    return idx in [5, 6, 11, 12]

  def poseapi_get_arm_keypoint_indexes(self):
    """
    Returns the indexes of the arm keypoints
    """
    return [5, 6, 7, 8, 9, 10]

  def poseapi_get_leg_keypoint_indexes(self):
    """
    Returns the indexes of the leg keypoints
    """
    return [11, 12, 13, 14, 15, 16]

  def poseapi_get_shoulder_keypoint_indexes(self):
    """
    Returns the indexes of the shoulder keypoints
    """
    return [5, 6]

  def poseapi_get_arm_indexes(self):
    """
    Alias for poseapi_get_arm_keypoint_indexes
    """
    return self.poseapi_get_arm_keypoint_indexes()

  def poseapi_get_leg_indexes(self):
    """
    Alias for poseapi_get_leg_keypoint_indexes
    """
    return self.poseapi_get_leg_keypoint_indexes()

  def poseapi_get_shoulder_indexes(self):
    """
    Alias for poseapi_get_shoulder_keypoint_indexes
    """
    return self.poseapi_get_shoulder_keypoint_indexes()


if __name__ == '__main__':
  import numpy as np
  import psutil
  import time
  import gc

  def get_random_kwargs():
    h, w = np.random.randint(100, 1000, 2)
    t, l = np.random.randint(0, 1000, 2)
    tlbr = [t, l, t + h, l + w]
    n_keypoints = np.random.randint(1, 100)
    kpt_with_conf = np.random.rand(n_keypoints, 3)
    to_flip = np.random.choice([True, False])
    inverse_keypoint_coords = np.random.choice([True, False])
    return {
      "tlbr": tlbr,
      "kpt_with_conf": kpt_with_conf,
      "to_flip": to_flip,
      "inverse_keypoint_coords": inverse_keypoint_coords
    }

  def test_iteration(obj, to_delete=False):
    kwargs = get_random_kwargs()
    keypoint_coords, keypoint_scores, height, width = obj.poseapi_extract_coords_and_scores(**kwargs)
    if to_delete:
      del keypoint_coords
    return

  def test_poseapi_extract_coords_and_scores(obj, n_tests=100):

    print(f"Testing poseapi_extract_coords_and_scores with {n_tests} attempts...")
    cnt_diff = [0, 0]
    gc.collect()
    time.sleep(10)
    initial_used_ram = psutil.virtual_memory()[3]
    print(f'Testing without delete[Initial {initial_used_ram}]')
    for _ in range(n_tests):
      test_iteration(obj)
    # endfor tests
    final_used_ram = psutil.virtual_memory()[3]
    delta_ram = (final_used_ram - initial_used_ram)
    time.sleep(10)
    print(f'Testing without delete[Final {final_used_ram}]')
    print(f'RAM Used: {(final_used_ram - initial_used_ram)}')
    gc.collect()
    time.sleep(10)

    initial_used_ram1 = psutil.virtual_memory()[3]
    print(f'Testing with delete[Initial {initial_used_ram1}]')
    for _ in range(n_tests):
      test_iteration(obj, to_delete=True)
    # endfor tests
    final_used_ram1 = psutil.virtual_memory()[3]
    delta_ram1 = (final_used_ram1 - initial_used_ram1)
    time.sleep(10)
    print(f'Testing with delete[Final {final_used_ram1}]')
    print(f'RAM Used: {(final_used_ram1 - initial_used_ram1)}')

    print(f"Testing poseapi_extract_coords_and_scores with {n_tests} attempts... DONE")
    return

  from decentra_vision import geometry_methods as gmt
  from copy import deepcopy
  obj = _PoseAPIMixin()
  obj.gmt = gmt
  obj.deepcopy = deepcopy
  test_poseapi_extract_coords_and_scores(obj, n_tests=100000)
