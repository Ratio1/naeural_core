import numpy as np
import torch as th
from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad

if __name__ == "__main__":
    img1 = np.zeros((600,400,3), dtype=np.uint8)
    img2 = np.zeros((1080,1920,3), dtype=np.uint8)
    
    th_sub = th.tensor([104, 117, 123]).unsqueeze(-1).unsqueeze(-1).to(th.device("cuda"))
    
    imgs = [img1, img2]
    th_x, dims = th_resize_with_pad(
        img=imgs, 
        h=720,
        w=1280,
        device=th.device("cuda"), 
        div_val=1,
        sub_val=th_sub, 
    )
    
    print("dev")
    