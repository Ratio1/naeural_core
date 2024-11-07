from kmonitor import KubeMonitor, safe_jsonify

if __name__ == '__main__':
  km = KubeMonitor()
  
  apps = ['emqx', 'nvidia', 'basic-test']
  
  result = km.check_pods_by_names(apps)  
  print("Pods {}:\n{}".format(apps, safe_jsonify(result, indent=2)))
  
  nodes = km.get_nodes_metrics()
  print("Nodes:\n{}".format(safe_jsonify(nodes, indent=2)))
  