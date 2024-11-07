import numpy as np
import matplotlib.pyplot as plt

if __name__ == '__main__':
  name_tp = 'TP'
  name_fp = 'FP'
  lst_thr = list(np.arange(0, 1.1, 0.1))
  lst_inf = [
    {
      'TLBR_POS': [1, 2, 3, 4], 
      'PROB_PRC': i/10, 
      'TYPE': 'person',
      'CLASS': 'TP' if i % 2 == 0 else 'FP'
    } for i in range(11)
    ]
  print(lst_inf)
  
  l_sensitivity, l_specificity = [], []
  lst_all_tp = [x for x in lst_inf if x['CLASS'] == name_tp]
  lst_all_fp = [x for x in lst_inf if x['CLASS'] == name_fp]
  for thr in lst_thr:
    l_thr = [x for x in lst_inf if x['PROB_PRC'] >= thr]
    lst_tp_thr = [x for x in l_thr if x['CLASS'] == name_tp]
    lst_fp_thr = [x for x in l_thr if x['CLASS'] == name_fp]
    lst_tn_thr = [x for x in lst_inf if x['PROB_PRC'] < thr and x['CLASS'] == name_tp]
    
    # Sensitivity OR TPR = TP / (TP + FN)
    sensitivity = len(lst_tp_thr) / len(lst_all_tp)
    
    # Specificity = TN / (TN + FP)
    specificity = len(lst_tn_thr) / (len(lst_tn_thr) + len(lst_fp_thr))
    
    l_sensitivity.append(sensitivity)
    l_specificity.append(specificity)
  #endfor
  
  fig = plt.figure()
  plt.plot(l_specificity, l_sensitivity)
  plt.xlabel('Specificity (False Positive Rate)')
  plt.ylabel('Sensitivity (True Positive Rate)')
  plt.show()
  
  
  