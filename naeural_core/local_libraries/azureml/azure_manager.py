__version__ = '1.0.0.0'

import os
import azureml
from naeural_client import BaseDecentrAIObject

from azureml.core import Workspace
from azureml.core.model import Model, InferenceConfig
from azureml.core.environment import Environment
from azureml.core.webservice import AciWebservice, Webservice
from azureml.core.authentication import ServicePrincipalAuthentication
try:
  from .enums import AzureLocationEnum
except:
  from azureml.enums import AzureLocationEnum
from time import time

class AuthenticationService(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def service_principal_authentication(self, tenant_id, service_principal_id, service_principal_password):
    """
    This method allows authentication into AzureML with service principal method

    Parameters
    ----------
    tenant_id : string
      AzureML tenant id .
    service_principal_id : TYPE
      AzureML client id (email).
    service_principal_password : TYPE
      AzureML client password.
    -------
    ServicePrincipalAuthentication object or throws exception

    """
    self.log.p('Making service principal authentication...')
    sp = ServicePrincipalAuthentication(
      tenant_id=tenant_id,
      service_principal_id=service_principal_id,
      service_principal_password=service_principal_password)
    sp.get_authentication_header()
    self.log.p('Done authentication', show_time=True)
    return sp


class WorkspaceService(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def create(self, auth, subscription_id, resource_group_name, workspace_name, location=AzureLocationEnum.EuropeWest.value):
    """
    Creates a new workspace based on the method arguments

    Parameters
    ----------
    subscription_id : string
      Account subscription id (guid format).
    resource_group_name : TYPE
      Name of the resource group that will be created.
    workspace_name : TYPE
      Name of the workspace.
    auth : TYPE
      Authentification object (ex servcice principal from the AuthenticationService).

    Returns
    -------
    ws : azure.core.Workspace
      Newly created workspace or throws exception

    """
    self.log.p('Creating new workspace with resource group name `{}` and workspace name `{}`'.format(resource_group_name, workspace_name))
    ws = Workspace.create(
      auth=auth,
      subscription_id=subscription_id,
      resource_group=resource_group_name,
      name=workspace_name,
      create_resource_group=True,
      location=location
      )
    self.log.p('Done creating workspace', show_time=True)
    return ws
  
  def create_from_config(self, path_config, auth):
    """
    Creates a workspace from a config file

    Parameters
    ----------
    path_config : str
      Path to config file.
    auth : ServicePrincipalAuthentication
      Authentication proviced.

    Returns
    -------
    ws : Workspace
      Newly created workspace or throws exception.

    """
    self.log.p('Creating new workspace from config')
    ws = Workspace.from_config(path=path_config, auth=auth)
    self.log.p('Done creating new workspace from config')
    return ws
  
  def delete(self, ws, delete_dependent_resources=True, no_wait=False):
    """
    Deletes specified workspace instance

    Parameters
    ----------
    ws : Workspace
      Workspace instance.
    delete_dependent_resources : boolean, optional
      Whether to delete
            resources associated with the workspace, i.e., container registry, storage account, key vault, and
            application insights. The default is False. Set to True to delete these resources.. The default is True.
    no_wait : TYPE, optional
      Wheather to wait or not for deletion to complete. The default is False.

    Returns
    -------
    None if successfull or throws exception

    """
    ws.delete(
      delete_dependent_resources=delete_dependent_resources, 
      no_wait=no_wait
      )
    return
  
  def get(self, auth, subscription_id, resource_group_name, workspace_name):
    ws = Workspace.get(
      auth=auth, 
      subscription_id=subscription_id,
      resource_group=resource_group_name,
      name=workspace_name
      )
    return ws
  
  def list_workspaces(self, auth, subscription_id, resource_group_name=None):
    """
    This method queries subscription_id and returns a dict of active workspaces

    Parameters
    ----------
    auth : Service Principal
      Authentication object.
    subscription_id : string
      AzureML subscription id.

    Returns
    -------
    dct : dictionary
      Dictinary containing the list of active workspaces in the provided subscription.

    """
    return Workspace.list(
      auth=auth, 
      subscription_id=subscription_id,
      resource_group=resource_group_name
      )
  

class ModelService(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def create(self, workspace, name, version=1, tags=None, properties=None, model_framework=None, **kwargs):
    """
    Creates a new model based on the provided arguments

    Parameters
    ----------
    workspace : azure.core.Workspace
      Workspace used to register the model container.
    name : string, optional
      Model name.
    tags : list, optional
      An optional list of tags used to filter returned results. Results are filtered based on the provided list, searching by either 'key' or '[key, value]'. Ex. ['key', ['key2', 'key2 value']]. The default is None.
    properties : list, optional
      An optional list of properties used to filter returned results. Results are filtered based on the provided list, searching by either 'key' or '[key, value]'. Ex. ['key', ['key2', 'key2 value']]. The default is None.
    version : integer
      Model version.
    model_framework : string, optional
      Framework used to train the model. The default is None.
    **kwargs : TYPE
      DESCRIPTION.

    Returns
    -------
    azureml.core.model
      Instance of azureml model.

    """
    return Model(
      workspace=workspace, 
      name=name, 
      tags=tags, 
      properties=properties,
      version=version, 
      model_framework=model_framework,
      kwargs=kwargs
      )
  
  def register(self, workspace, model_path, model_name, description, tags=None, properties=None, model_framework=None):
    """
    This method handles the model container registration. You should perceive this "model" as a container/object that will be deployed on the workspace togheter with one or more assets

    Parameters
    ----------
    workspace : azureml.core workspace
      Workspace used to deploy the current model.
    model_path : string
      Path to assets: either a specific file (saved model pkl/h5/etc) or path to a folder that contain assets (model1.h5 + model2.pkl + model3.pb + data1.pkl + data2.csv + etc). 
    model_name : string
      Model name.
    description : string
      Description of the model.
    tags : list, optional
      An optional list of tags used to filter returned results. Results are filtered based on the provided list, searching by either 'key' or '[key, value]'. Ex. ['key', ['key2', 'key2 value']]. The default is None.
    properties : list, optional
      An optional list of properties used to filter returned results. Results are filtered based on the provided list, searching by either 'key' or '[key, value]'. Ex. ['key', ['key2', 'key2 value']]. The default is None.
    model_framework : TYPE, optional
      Framework used to develop/train the model. The default is None.

    Returns
    -------
    model : azureml.core Model
      Model container registered on the specified workspace.

    """
    assert os.path.exists(model_path)
    if os.path.isdir(model_path):
      assert len(os.listdir(model_path)) > 0
      
    model = Model.register(
      workspace=workspace, 
      model_path=model_path, 
      model_name=model_name, 
      tags=tags, 
      properties=properties, 
      description=description,
      model_framework=model_framework)
    return model
  
  def create_inference_config(self, entry_script_path, environment, source_directory=None):
    """
    This method creates inference config object based on the entry script and environment

    Parameters
    ----------
    entry_script_path : str
      Path to the script that will be used to make inference.
    environment : azureml environment
      Environment that will be used to make inference.

    Returns
    -------
    InferenceConfig
      Inference config object that can be later used to deploy/update a specific ACI container.

    """
    if source_directory:
      assert os.path.exists(os.path.join(source_directory, entry_script_path))
    else:
      assert os.path.exists(entry_script_path)
    return InferenceConfig(
      entry_script=entry_script_path, 
      environment=environment,
      source_directory=source_directory
      )

  def deploy(self, workspace, name, models, inference_config, deployment_config):
    """
    This method deploys a webservice as a real-time endpoint that can be used to make inference request for the models provided

    Parameters
    ----------
    workspace : azureml workspace
      A Workspace object to associate the Webservice with.
    name : str
      The name to give the deployed service. Must be unique to the workspace, only consist of lowercase letters, numbers, or dashes, start with a letter, and be between 3 and 32 characters long..
    models : list
      A list of model objects. Can be an empty list.
    inference_config : azureml inferenceconfig
      An InferenceConfig object used to determine required model properties.
    deployment_config : azureml WebserviceDeploymentConfiguration
      A WebserviceDeploymentConfiguration used to configure the webservice

    Returns
    -------
    None.

    """
    service = Model.deploy(
      workspace=workspace,
      name=name,
      models=models,
      inference_config=inference_config,
      deployment_config=deployment_config
      )
    service.wait_for_deployment(show_output=True)
    return service


class EnvironmentService(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def create_from_conda_specification(self, workspace, env_name, file_path):
    """
    Create environment object from an environment specification YAML file.
    Ex:
    name: inference_environment
    dependencies:
      - python=3.6.2
      - tensorflow>=2.0.0
      - pip:
        - azureml-defaults>=1.0.45
        
    Parameters
    ----------
    workspace : azureml.core workspace
      Current workspace used to deploy the model.
    env_name : string
      Conda environment name.
    file_path : TYPE
      Path to YAML config file.
      
    Returns
    -------
    conda_env : azureml.core Environemnt
      Registered environment container.

    """
    assert os.path.exists(file_path)
    assert file_path.endswith('.yaml'), 'Provided config should be yaml format'
    
    conda_env = Environment.from_conda_specification(
      name=env_name, 
      file_path=file_path
      )
    env = conda_env.register(workspace=workspace)
    return env
  
  def get(self, workspace, name, version=None):
    """
    Gets the environment specified by workspace and name

    Parameters
    ----------
    workspace : azureml workspace
      Workspace container.
    name : string
      Name of environment.
    version: string
      Environment version
      
    Returns
    -------
    Environment
      Returns the specified environment.

    """
    return Environment.get(workspace=workspace, name=name, version=version)
  
  def list_environments(self, workspace):
    """
    This method returns all the environments associated with a specific workspace

    Parameters
    ----------
    workspace : azureml workspace

    Returns
    -------
    Dict
      Dictinoary containing environments created in the specified workspace.

    """
    return Environment.list(workspace=workspace)


class AciService(BaseDecentrAIObject):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    return
  
  def deploy_configuration(self, cpu_cores, memory_gb):
    """
    This method creates a configuration object with hardware specifications

    Parameters
    ----------
    cpu_cores : int
      Number of cpu cores.
    memory_gb : int
      Memory size.

    Returns
    -------
    service : AciServiceDeploymentConfiguration
      Config object with hardware resources.

    """
    service = AciWebservice.deploy_configuration(
      cpu_cores=cpu_cores, 
      memory_gb=memory_gb
      )
    return service
  
  def list_webservices(self, workspace):
    """
    List all the webservices associated with the provided workspace

    Parameters
    ----------
    workspace : azureml workspace
    Returns
    -------
    List of 
      List with all webservices associated with the provided workspace.

    """
    return AciWebservice.list(workspace=workspace)
  
  def delete(self, workspace, service_name):
    """
    This method deletes specified aci_service from the specified workspace) 

    Parameters
    ----------
    workspace : azureml workspace
      Workspace used to delete the aci webservice.
    service_name : str
      Service to be deleted

    Returns
    -------
    None.

    """
    try:
      service = self.get(workspace=workspace, service_name=service_name)
      service.delete()
    except Exception as e:
      self.log.p('There was an exception while deleting aci webservice: {}'.format(str(e)))
    return
  
  def get(self, workspace, service_name):
    """
    This method retrieves aci_service from the specified workspace

    Parameters
    ----------
    workspace : azureml workspace
      Workspace used to delete the aci webservice.
    service_name : str
      Service to be identified

    Returns
    -------
    AciWebservice.

    """
    return Webservice(workspace=workspace, name=service_name)


class AzureManager(BaseDecentrAIObject):
  def __init__(self, subscription_id, tenant_id, client_id, client_secret, **kwargs):
    """
    

    Parameters
    ----------
    subscription_id : String
      String containing Azure Subscription ID.
    tenant_id : String 
      String containing Azure Tenant ID.
    client_id : String
      String containing Azure Client ID.
    client_secret : String
      String containing Azure Client Secret.
    **kwargs : Dict
      BaseDecentrAIObject kwargs.

    Returns
    -------
    None.

    """
    self.__version__ = __version__
    self.version = __version__    
    self.__name__ = "AzureMLManager"
    
    super().__init__(**kwargs)
    self.subscription_id = subscription_id
    self.tenant_id = tenant_id
    self.client_id = client_id
    self.client_secret = client_secret
    
    self._auth()
    self.wss = WorkspaceService(log=self.log)
    self.envs = EnvironmentService(log=self.log)
    self.acis = AciService(log=self.log)
    self.ms = ModelService(log=self.log)
    return

  def _auth(self):
    auth = AuthenticationService(log=self.log)
    self.auth_sp = auth.service_principal_authentication(
      tenant_id=self.tenant_id,
      service_principal_id=self.client_id,
      service_principal_password=self.client_secret
      )
    return
  
  def _get_workspace(self, resource_group_name, workspace_name):
    ws = None
    try:
      dct_ws = self.wss.list_workspaces(
        auth=self.auth_sp, 
        subscription_id=self.subscription_id
        )
      if workspace_name in dct_ws:
        ws = self.wss.get(
          auth=self.auth_sp,
          subscription_id=self.subscription_id, 
          resource_group_name=resource_group_name, 
          workspace_name=workspace_name
          )
    except:
      self.log.p('Resource group does not exists')
    return ws
  
  def _create_workspace(self, resource_group_name, workspace_name):
    dct_ws = {}
    try:
      dct_ws = self.wss.list_workspaces(
        auth=self.auth_sp, 
        subscription_id=self.subscription_id,
        resource_group=resource_group_name
        )
    except:
      pass
    assert workspace_name not in dct_ws, 'Workspace name you provided {} already exist in the resource group {}'.format(workspace_name, resource_group_name)
   
    ws = self.wss.create(
      subscription_id=self.subscription_id,
      resource_group_name=resource_group_name,
      workspace_name=workspace_name,
      auth=self.auth_sp
      )
    return ws
  
  def _get_conda_env(self, workspace, inference_env_name):
    dct_envs = self.envs.list_environments(workspace=workspace)
    env_conda = None
    if inference_env_name in dct_envs:
      env_conda = self.envs.get(workspace=workspace, name=inference_env_name)
    return env_conda
  
  def _create_conda_env(self, workspace, inference_env_name, conda_config_path):
    dct_envs = self.envs.list_environments(workspace=workspace)
    assert inference_env_name not in dct_envs, 'Inference env name provided already exists in the workspace: {}'.format(inference_env_name)
    
    env_conda = self.envs.create_from_conda_specification(
      workspace=workspace, 
      env_name=inference_env_name, 
      file_path=conda_config_path
      )
    return env_conda
  
  def new_webservice(self, resource_group_name, workspace_name, 
                     inference_env_name, conda_config_path, 
                     scoring_path, webservice_name, cpu_cores, memory_gb, 
                     model_path, model_name, model_tags=None, model_description=None,
                     scoring_source_directory=None):
    """
    This method creates a new webservice in Azure, by automatically allocating hardware and creating the inference environment.

    Parameters
    ----------
    resource_group_name : String
      The name of the existing or future Azure resource group.
    workspace_name : String
      The name of the existing or future Azure workspace.
    cpu_cores : Int
      The number of CPU cores to allocate to the future Azure container.
    memory_gb : Int
      The memory to allocate to the future Azure Container.
    inference_env_name : String
      The name of the (inference) environment.
    conda_config_path : String
      Path to the .yaml conda config file.
    scoring_path : String
      The name of the scoring script (.py script)
    model_path : String
      Path to the model.
    model_name : String
      Model name.
    model_tags : Dict
      Dictionary containing tags describing the model.
    model_description : String
      Description of the model.
    webservice_name : String
      The name for the final created webservice.
    scoring_source_directory: String
      The path to entire data structure (folder) to be uploaded into Azure
    
    Returns
    -------
    service : Azure Webservice
      Service that can be accessed and used to make predictions.

    """
    t_start = time()
    self.log.p('Deploying new webservice')
    self.log.p(' * Resource Group Name: ' + resource_group_name)
    self.log.p(' * Workspace Name: ' + workspace_name)
    self.log.p(' * Inference Env Name: ' + inference_env_name)
    self.log.p(' * Webservice Name: ' + webservice_name)
    #1. get or create workspace & resource group
    ws = self._get_workspace(resource_group_name, workspace_name)
    if not ws:
      ws = self._create_workspace(resource_group_name, workspace_name)
    self.log.p('Workspace loaded', show_time=True)
    
    #2. get or create the required conda environmnet for model
    env_conda = self._get_conda_env(ws, inference_env_name)
    if not env_conda:
      env_conda = self._create_conda_env(ws, inference_env_name, conda_config_path)
    self.log.p('Conda environment loaded', show_time=True)
    #3. deploy resources
    aci_config = self.acis.deploy_configuration(cpu_cores, memory_gb)
    self.log.p('Deploy configuration loaded', show_time=True)
    
    #4. create inference config based on scoring script and environment
    inf_conf = self.ms.create_inference_config(
      scoring_path, 
      env_conda, 
      scoring_source_directory
      )
    self.log.p('Inference configuration loaded', show_time=True)
    
    #5. register the assets (models, etc) needed to make inference
    model = self.ms.register(
      workspace=ws, 
      model_path=model_path,
      model_name=model_name,
      tags=model_tags,
      description=model_description
      )
    self.log.p('Model registered', show_time=True)
    
    #6. deploy
    service = self.ms.deploy(
      workspace=ws,
      name=webservice_name,
      models=[model],
      inference_config=inf_conf,
      deployment_config=aci_config
      )
    self.log.p('Model deployed', show_time=True)
    t_stop = time()
    self.log.p('Total time: {}'.format(t_stop - t_start))
    return service
  
  def update_webservice(self, resource_group_name, workspace_name, inference_env_name, 
                        webservice_name, scoring_path, 
                        model_path, model_name, model_tags=None, model_description=None,
                        scoring_source_directory=None):
    """
    This method updates and existing webservice by uploading a new model and a new scoring script.

    Parameters
    ----------
    resource_group_name : String
      The name of the existing or future Azure resource group.
    workspace_name : String
      The name of the existing or future Azure workspace.
    inference_env_name : String
      The name of the (inference) environment.
    scoring_path : String
      The name of the scoring script (.py script)
    model_path : String
      Path to the model.
    model_name : String
      Model name.
    model_tags : Dict
      Dictionary containing tags describing the model.
    model_description : String
      Description of the model.
    webservice_name : String
      The name for the final created webservice.
    scoring_source_directory: String
      The path to entire data structure (folder) to be uploaded into Azure

    Returns
    -------
    webservice : Azure Webservice
      New service updated and ready to be used for inference.

    """
    t_start = time()
    self.log.p('Updating webservice:')
    self.log.p(' * Resource Group Name: ' + resource_group_name)
    self.log.p(' * Workspace Name: ' + workspace_name)
    self.log.p(' * Inference Env Name: ' + inference_env_name)
    self.log.p(' * Webservice Name: ' + webservice_name)
    #1. check and get the workspace
    dct_ws = self.wss.list_workspaces(auth=self.auth_sp, subscription_id=self.subscription_id)
    assert workspace_name in dct_ws, 'The workspace you provided does not exist on this subscription!'
    workspace = self._get_workspace(resource_group_name, workspace_name)
    self.log.p('Workspace loaded', show_time=True)
    
    #2. check and get the (inference) environment
    dct_envs = self.envs.list_environments(workspace=workspace)
    assert inference_env_name in dct_envs, 'The inference environment you provided does not exist on this workspace'
    env_conda = self._get_conda_env(workspace, inference_env_name)
    self.log.p('Environment loaded', show_time=True)
    
    #3. check and get the existing webservice
    webservice = self.acis.get(workspace, webservice_name)
    assert webservice is not None, 'Service {} does not exist in the current workspace {}'.format(webservice_name, workspace_name)
    self.log.p('Webservice loaded', show_time=True)
    
    #4. creating new inference config with the new score.py
    new_inference_config = self.ms.create_inference_config(
      scoring_path, 
      env_conda, 
      scoring_source_directory)
    self.log.p('New inference config created', show_time=True)
    
    #5. creating new model
    new_model = self.ms.register(
      workspace=workspace,
      model_path=model_path,
      model_name=model_name,
      tags=model_tags,
      description=model_description
      )
    self.log.p('New model registered', show_time=True)
    
    webservice.update(models=[new_model], inference_config=new_inference_config)
    webservice.wait_for_deployment(show_output=True)
    t_stop = time()
    self.log.p('Total time: {}'.format(t_stop - t_start))
    return webservice
    


if __name__ == '__main__':

  def test():
    headers = {'Content-Type': 'application/json'}
    payload = json.dumps({"text": "am fost la un restaurant cu muzica placuta si personal dragut. ciorba lor de legume era delicioasa. fructele lor de mare erau din alta lume. caviarul a fost delicios. am uitat sa mentionez ca restaurantul era pe Calea Dorobanti"})
    response = requests.post(service.scoring_uri, data=payload, headers=headers)
    log.p('The service can be called at: {}'.format(service.scoring_uri))
    log.p('Service responded with code: {}'.format(response.status_code))
    log.p('Service responded in: {}'.format(response.elapsed))
    log.p('Service response: {}'.format(response.json()))
    return

  import json
  import requests
  from logger import Logger

  log = Logger(lib_name='AZRML', config_file='config.txt')
  path_data = os.path.join(log.get_data_folder(), 'azureml')
  
  AZURE_SUBSCRIPTION_ID = '21862221-de06-45b9-b99b-94e0a3c69193'
  AZURE_TENANT_ID       = '6f2b3c9b-f251-47cd-add5-3c3bca51b59b'
  AZURE_CLIENT_ID       = '5e548f9b-4884-40cd-98b0-c18676a29f62'
  AZURE_CLIENT_SECRET   = 'FR1HX35VS_ssGmiYsBO5..ogl1a.83g0cx'

  resource_group_name       = 'HODS3'
  workspace_name            = 'Lecture8'
  inference_env_name        = 'hods_env'
  cpu_cores                 = 1
  memory_gb                 = 1
  conda_config_path         = os.path.join(path_data, 'assets', 'config.yaml')
  model_path                = os.path.join(path_data, 'models')
  model_name                = 'restocracy'
  model_tags                = {'restocracy': 'demo'}
  model_description         = 'Model trained on restocracy reviews'
  webservice_name           = 'restocracyapi'
  scoring_source_directory  = None
  scoring_path              = os.path.join(path_data, 'test1', 'score.py')

  azure_mng = AzureManager(
    log=log, 
    subscription_id=AZURE_SUBSCRIPTION_ID,
    tenant_id=AZURE_TENANT_ID,
    client_id=AZURE_CLIENT_ID,
    client_secret=AZURE_CLIENT_SECRET
    )
  
  #Test case 1:
  #We have a new model and we need to fastly serve it in Azure
  # For that we are going to create a new webservice
  # Create webservice
  service = azure_mng.new_webservice(
    resource_group_name=resource_group_name, 
    workspace_name=workspace_name, 
    inference_env_name=inference_env_name, 
    conda_config_path=conda_config_path, 
    webservice_name=webservice_name,
    cpu_cores=cpu_cores, 
    memory_gb=memory_gb, 
    scoring_path=scoring_path,
    scoring_source_directory=scoring_source_directory,
    model_path=model_path, 
    model_name=model_name, 
    model_tags=model_tags, 
    model_description=model_description
    )
  test()
  
  #Test case 2:
  #After some days we decide to update the scoring script in order to respond with a json containing the model version.
  #So we do some updates on our scoring script and after that we are updating our webservice
  # Update webservice
  scoring_path = os.path.join(path_data, 'test2', 'score.py')
  service = azure_mng.update_webservice(
    resource_group_name=resource_group_name, 
    workspace_name=workspace_name, 
    inference_env_name=inference_env_name, 
    webservice_name=webservice_name,
    scoring_path=scoring_path,
    scoring_source_directory=scoring_source_directory,
    model_path=model_path,
    model_name=model_name,
    model_tags=model_tags,
    model_description=model_description
    )
  test()
  
  #Test case 3:
  #After a week of using the version 2 of our scoring/model, we decide to include logger in our deployment.
  #The key aspect here is to specify that you have an entire directory that you want to upload
  #The scoring script is still inside that directory
  
  scoring_source_directory = os.path.join(path_data, 'test3')
  scoring_path = 'score.py'
  
  # Update webservice
  service = azure_mng.update_webservice(
    resource_group_name=resource_group_name, 
    workspace_name=workspace_name, 
    inference_env_name=inference_env_name, 
    webservice_name=webservice_name,
    scoring_path=scoring_path,
    scoring_source_directory=scoring_source_directory,
    model_path=model_path,
    model_name=model_name,
    model_tags=model_tags,
    model_description=model_description
    )
  test()

  # service.get_logs()

