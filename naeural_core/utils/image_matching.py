import cv2
import numpy as np

# Build SIFT descriptors. Return keypoints and features
def get_features(frame):
  # The grayscale version of the image is considered for processing
  trainImg_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

  descriptor = None
  try:
    descriptor = cv2.SIFT_create()
  except:
    try:
      descriptor = cv2.xfeatures2d.SURF_create()
    except:
      pass
  
  if descriptor is None:
    raise ValueError('Could not calculate descriptor!')
  
  # Compute keypoints and features of the grayscale image
  kpsA, featuresA = descriptor.detectAndCompute(trainImg_gray, None)
  return (kpsA, featuresA)


# KNN for matching keypoints from anchor and test images
def match_keypoints_KNN(featuresA, featuresB, ratio):
  # Use BruteForce method to find matches and Euclidean norm
  bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

  # __get_features() can return them None if image is obstructed
  if featuresA is not None and featuresB is not None:
    # Find the best 2 neighbours of each feature
    rawMatches = bf.knnMatch(featuresA, featuresB, 2)
    matches = []

    # Get only the matches that have a distance between them smaller than a threshold
    for mn in rawMatches:
      if len(mn) < 2:
        return None
      m, n = mn
      if m.distance < n.distance * ratio:
        matches.append(m)
    return matches
  # endif
  return None


# Build homography transformation in order to get the translation between images
def get_homography(kpsA, kpsB, matches, reprojThresh):
  # To float32
  kpsA = np.float32([kp.pt for kp in kpsA])
  kpsB = np.float32([kp.pt for kp in kpsB])

  # If homography transformation is possible
  if len(matches) > 4:
    # Get keypoints coordinates from matches
    ptsA = np.float32([kpsA[m.queryIdx] for m in matches])
    ptsB = np.float32([kpsB[m.trainIdx] for m in matches])

    # RANSAC algorithm is used to compute the best combinations between matches in order not to try all the possibilities
    (H, status) = cv2.findHomography(ptsA, ptsB, cv2.RANSAC,
                                     reprojThresh)

    return (matches, H, status)
  return None