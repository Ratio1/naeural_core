import cv2


from naeural_core.utils.basic_qr_reader import read_qr
  

if __name__ == '__main__':
  
  fns = [
    # 'xperimental/qr_codes/test1.png',
    # 'xperimental/qr_codes/test2.png',
    'xperimental/qr_codes/test3.png',
    'xperimental/qr_codes/pic1.jpg',
    'xperimental/qr_codes/pic2.jpg',
    ]
  
  for fn in fns:
    np_rgb_img = cv2.imread(fn)[:,:,::-1]
    cv2.imshow("img", np_rgb_img[:,:,::-1])
    cv2.waitKey(0)
    cv2.destroyAllWindows()
      
    ###
  
    res = read_qr(np_rgb_img) 
    
    if res is not None:
      data, tlbr, bbox, str_qr, np_slice = res
      
      if bbox is not None:
        print(f"QRCode for {fn}:\n{data}")
        # display the image with lines
        # length of bounding box
        n_lines = len(bbox)
        for i in range(n_lines):
          # draw all lines
          point1 = tuple(bbox[i][0].astype('int').tolist())
          point2 = tuple(bbox[(i+1) % n_lines][0].astype('int').tolist())
          cv2.line(np_rgb_img, point1, point2, color=0, thickness=5)  
        
      
      cv2.imshow("slice", np_slice)
      cv2.waitKey(0)
      cv2.destroyAllWindows()