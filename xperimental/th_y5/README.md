# Torchscript stuff !

```
@try_export
def export_torchscript(model, im, file, optimize, prefix=colorstr('TorchScript:')):
    # YOLOv5 TorchScript model export
    LOGGER.info(f'\n{prefix} starting export with torch {torch.__version__}...')
    h, w = im.shape[-2:]
    model_param = next(model.parameters())
    model_dev = model_param.device 
    model_dtype = str(model_param.dtype).split('.')[-1]    
    batch_size = im.shape[0]
    stride = int(max(model.stride))
    dev_type = model_dev.type
    dev_index = ''
    if dev_type == 'cuda':
      dev_index = ":" + str(model_dev.index) if model_dev.index is not None else ":0"
    pyver = sys.version.split()[0].replace('.','')
    thver = torch.__version__.replace('.','')
    model_name = file.name.split('.')[0]    
    model_date = time.strftime('%Y%m%d', time.localtime(time.time()))
    
    d = {
      'shape'   : im.shape, 
      'stride'  : stride, 
      'batch'   : batch_size,
      # 'names'   : model.names,
      'python'  : pyver,
      'torch'   : thver,
      'device'  : dev_type + dev_index,
      'optimize': optimize,
      'date'    : model_date,
      'name'    : model_name,
    }   
    folder = str(file.parent)
    fn = os.path.join(folder, '{}_{}_{}x{}_th{}_py{}_{}{}.ths'.format(
      model_date, model_name, h, w, thver, pyver, 
      model_dtype, dev_type + dev_index.replace(':','')
    ))
    fn = fn.replace('yolov','y').replace('float','f').replace('cuda','cu')
    f = Path(fn)
    
    ts = torch.jit.trace(model, im, strict=False)
    extra_files = {'config.txt': json.dumps(d)}  # torch._C.ExtraFilesMap()
    if optimize:  # https://pytorch.org/tutorials/recipes/mobile_interpreter.html
        optimize_for_mobile(ts)._save_for_lite_interpreter(fn, _extra_files=extra_files)
    else:
        ts.save(fn, _extra_files=extra_files)
    return f, None
```    
    
```
def parse_opt(known=False, imgsz=None, model_name=None, dev='0'):
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=str, default=ROOT / 'data/coco128.yaml', help='dataset.yaml path')
    parser.add_argument('--weights', nargs='+', type=str, default=ROOT / model_name, help='model.pt path(s)')
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=imgsz, help='image (h, w)')
    parser.add_argument('--batch-size', type=int, default=1, help='batch size')
    parser.add_argument('--device', default=dev, help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--half', action='store_true', help='FP16 half-precision export')
    parser.add_argument('--inplace', action='store_true', help='set YOLOv5 Detect() inplace=True')
    parser.add_argument('--keras', action='store_true', help='TF: use Keras')
    parser.add_argument('--optimize', action='store_true', help='TorchScript: optimize for mobile')
    parser.add_argument('--int8', action='store_true', help='CoreML/TF INT8 quantization')
    parser.add_argument('--dynamic', action='store_true', help='ONNX/TF/TensorRT: dynamic axes')
    parser.add_argument('--simplify', action='store_true', help='ONNX: simplify model')
    parser.add_argument('--opset', type=int, default=17, help='ONNX: opset version')
    parser.add_argument('--verbose', action='store_true', help='TensorRT: verbose log')
    parser.add_argument('--workspace', type=int, default=4, help='TensorRT: workspace size (GB)')
    parser.add_argument('--nms', action='store_true', help='TF: add NMS to model')
    parser.add_argument('--agnostic-nms', action='store_true', help='TF: add agnostic NMS to model')
    parser.add_argument('--topk-per-class', type=int, default=100, help='TF.js NMS: topk per class to keep')
    parser.add_argument('--topk-all', type=int, default=100, help='TF.js NMS: topk for all classes to keep')
    parser.add_argument('--iou-thres', type=float, default=0.45, help='TF.js NMS: IoU threshold')
    parser.add_argument('--conf-thres', type=float, default=0.25, help='TF.js NMS: confidence threshold')
    parser.add_argument(
        '--include',
        nargs='+',
        default=['torchscript'],
        help='torchscript, onnx, openvino, engine, coreml, saved_model, pb, tflite, edgetpu, tfjs, paddle')
    opt = parser.parse_known_args()[0] if known else parser.parse_args()
    print_args(vars(opt))
    return opt


def main(opt):
    for opt.weights in (opt.weights if isinstance(opt.weights, list) else [opt.weights]):
        run(**vars(opt))


if __name__ == '__main__':
  MODELS = [
    {
      'imgsz' : [896, 1280],
      'model_name' : 'yolov5l6.pt',
      'dev' : '0',
    },
    {
      'imgsz' : [576, 960],
      'model_name' : 'yolov5s6.pt',
      'dev' : '0',
    },    
  ]
  for m in MODELS:
    opt = parse_opt(**m)
    main(opt)
    
```    

 
NMS
```
  """
  Perform non-maximum suppression (NMS) on a set of boxes, with support for masks and multiple labels per box.

  Arguments:
      prediction (torch.Tensor): A tensor of shape (batch_size, num_boxes, num_classes + 4 + num_masks)
          containing the predicted boxes, classes, and masks. The tensor should be in the format
          output by a model, such as YOLO.
      conf_thres (float): The confidence threshold below which boxes will be filtered out.
          Valid values are between 0.0 and 1.0.
      iou_thres (float): The IoU threshold below which boxes will be filtered out during NMS.
          Valid values are between 0.0 and 1.0.
      classes (List[int]): A list of class indices to consider. If None, all classes will be considered.
      agnostic (bool): If True, the model is agnostic to the number of classes, and all
          classes will be considered as one.
      multi_label (bool): If True, each box may have multiple labels.
      labels (List[List[Union[int, float, torch.Tensor]]]): A list of lists, where each inner
          list contains the apriori labels for a given image. The list should be in the format
          output by a dataloader, with each label being a tuple of (class_index, x1, y1, x2, y2).
      max_det (int): The maximum number of boxes to keep after NMS.
      nc (int): (optional) The number of classes output by the model. Any indices after this will be considered masks.
      max_time_img (float): The maximum time (seconds) for processing one image.
      max_nms (int): The maximum number of boxes into torchvision.ops.nms().
      max_wh (int): The maximum box width and height in pixels

  Returns:
      (List[torch.Tensor]): A list of length batch_size, where each element is a tensor of
          shape (num_boxes, 6 + num_masks) containing the kept boxes, with columns
          (x1, y1, x2, y2, confidence, class, mask1, mask2, ...).
  """
```