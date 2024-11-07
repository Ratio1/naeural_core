import cv2 as cv
import cProfile


VIDEO_PATHS = [
  '/home/bleo/debug/1',
  '/home/bleo/debug/2',
  '/home/bleo/debug/3',
  '/home/bleo/debug/4',
  # '/home/bleo/debug/telefon_1.mp4',
  # '/home/bleo/debug/telefon_2.mp4',
  # '/home/bleo/debug/telefon_3.mp4',
  # '/home/bleo/debug/telefon_4.mp4',
  # '/home/bleo/debug/car demo.mp4',
  # '/home/bleo/debug/Test_2.mp4',
  # '/home/bleo/debug/Test 2 - Portret 1 - 608p.mp4',
  # '/home/bleo/debug/Test 2 - Portret 2 - 144p.mp4',
  # '/home/bleo/debug/Test 2 - Portret 3 - 240p.mp4',
  # '/home/bleo/debug/Test 2 - Portret 4 - 240p.mp4',
  # '/home/bleo/debug/Test 2 - Portret 5 - 608p.mp4'
]

N_LAPS = 100


def get_rotate_code_from_orientation(cap_orientation, for_undo=False):
  rotate_code = None
  if int(cap_orientation) == 270:
    rotate_code = cv.ROTATE_90_CLOCKWISE
  elif int(cap_orientation) == 180:
    rotate_code = cv.ROTATE_180
  elif int(cap_orientation) == 90:
    rotate_code = cv.ROTATE_90_COUNTERCLOCKWISE

  if rotate_code is not None and for_undo:
    rotate_code = 2 - rotate_code
  return rotate_code


def correct_rotation(frame, rotateCode):
  if rotateCode is None:
    return frame
  return cv.rotate(frame, rotateCode)


def original_video_reader(video_path, to_show=False):
  cap = cv.VideoCapture(video_path)

  while cap.isOpened():
    ret, frame = cap.read()
    if ret == True:
      if to_show:
        cap_orientation = cap.get(cv.CAP_PROP_ORIENTATION_META)
        cv.imshow(f'OriginalReader_{cap_orientation}', frame)
        pressed_key = cv.waitKey(25) & 0xFF
        # Press Q on keyboard to  exit
        if pressed_key == ord('q'):
          break
    # Break the loop
    else:
      break

  # When everything done, release the video capture object
  cap.release()
  # Closes all the frames
  cv.destroyAllWindows()
  return


def good_rotation_video_reader(video_path, to_show=False):
  cap = cv.VideoCapture(video_path)
  cap.set(cv.CAP_PROP_ORIENTATION_AUTO, 0)
  cap_orientation = cap.get(cv.CAP_PROP_ORIENTATION_META)
  rotate_code = get_rotate_code_from_orientation(cap_orientation)

  while cap.isOpened():
    ret, frame = cap.read()
    if ret == True:
      if rotate_code is not None:
        frame = correct_rotation(frame, rotate_code)

      if to_show:
        cv.imshow(f'UpdatedReader_{cap_orientation}', frame)
        pressed_key = cv.waitKey(25) & 0xFF
        # Press Q on keyboard to  exit
        if pressed_key == ord('q'):
          break
    # Break the loop
    else:
      break
  # When everything done, release the video capture object
  cap.release()
  # Closes all the frames
  cv.destroyAllWindows()
  return


def check_video(video_path):
  original_video_reader(video_path=video_path, to_show=True)
  good_rotation_video_reader(video_path=video_path, to_show=True)

  return

def main1():
  for _ in range(N_LAPS):
    for path in VIDEO_PATHS:
      original_video_reader(path)
  return


def main2():
  for _ in range(N_LAPS):
    for path in VIDEO_PATHS:
      good_rotation_video_reader(path)
  return


def main(video_path):
  cap = cv.VideoCapture(video_path)
  cap.set(cv.CAP_PROP_ORIENTATION_AUTO, 0)
  cap_orientation = cap.get(cv.CAP_PROP_ORIENTATION_META)
  rotate_code = get_rotate_code_from_orientation(cap_orientation)

  sz = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
  center_frame_id = int(sz // 2)
  back_frame_id = int(sz * 0.1)
  forward_frame_id = int(sz * 0.9)


  while cap.isOpened():
    ret, frame = cap.read()
    if ret == True:

      if rotate_code is not None:
        frame = correct_rotation(frame, rotate_code)
      # Display the resulting frame
      cv.imshow('Video', frame)

      pressed_key = cv.waitKey(25) & 0xFF
      # Press Q on keyboard to  exit
      if pressed_key == ord('q'):
        break
      elif pressed_key == ord('c'):
        cap.set(cv.CAP_PROP_POS_FRAMES, center_frame_id-1)
      elif pressed_key == ord('b'):
        cap.set(cv.CAP_PROP_POS_FRAMES, back_frame_id-1)
      elif pressed_key == ord('f'):
        cap.set(cv.CAP_PROP_POS_FRAMES, forward_frame_id-1)

    # Break the loop
    else:
      break

  # When everything done, release the video capture object
  cap.release()

  # Closes all the frames
  cv.destroyAllWindows()


if __name__ == '__main__':
  # video_paths = [
  #   '/home/bleo/debug/car demo.mp4',
  #   '/home/bleo/debug/Test_2.mp4',
  #   '/home/bleo/debug/Test 2 - Portret 1 - 608p.mp4',
  #   '/home/bleo/debug/Test 2 - Portret 2 - 144p.mp4'
  # ]
  # video_path = '/home/bleo/debug/car demo.mp4'
  # video_path = '/home/bleo/debug/Test_2.mp4'
  # video_path = '/home/bleo/debug/Test 2 - Portret 1 - 608p.mp4'
  # video_path = '/home/bleo/debug/Test 2 - Portret 2 - 144p.mp4'
  #
  # dps = 25
  # main(video_path=video_path)
  # main1()
  # cProfile.run('main1()')
  # cProfile.run('main2()')

  for path in VIDEO_PATHS:
    check_video(path)


