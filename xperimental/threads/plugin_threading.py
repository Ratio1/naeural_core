# -*- coding: utf-8 -*-
"""

Pseudo-code for business plugin threading design.

1. Plugin modification adds deque of upstream data and a loop method (thread target)
2. Manager justs adds data to plugin queues
Now the hard part:
3. Communicator manager on the other hand must be a thread that continously checks for available payloads
  thus the payloads must be

"""


# ALMOST DONE
# - TODO - individual queues must be monitored by the AppMonitor and and alerts should be sent if any plugin is too lazy
# - TODO - implement _on_idle in jobs plugins (blur, object-detector-tracking)
class Plugin():

    # def add_inputs(self, inputs):
    #   self.upstream_data_deque.append(inputs)
    #   return

    def _on_data(self):
        # default behaviour execution chain but can be overwritten
        self.execution_chain()
        return

    def _on_idle(self):
        # can be overwritten
        return

    def _on_init(self):
        # can be overwritten
        return

    def _on_close(self):
        # can be overwritten
        return

    def plugin_loop(self):
        # thread control
        self.done = False
        # one timer
        try:
            self._on_init()
            self.P("Thread ... initialized")
            # loop
            while not self.done:
                # upstream data is appended to a deque
                if len(self.upstream_data_deque) > 0:
                    # if data then call "On Data"
                    # IMPORTANT: individual queues must be monitored by the AppMonitor
                    #            and allerts should be sent if any plugin is too lazy
                    self.inputs = self.upstream_data_deque.popleft()
                    self.start_timer('on_data')
                    self._on_data()
                    self.stop_timer('on_data')
                else:
                    # call "On Idle"
                    self.start_timer('on_idle')
                    self._on_idle()
                    self.stop_timer('on_idle')

                # now we may have some output
                payload = self.get_payload_after_exec()
                commands = self.get_commands_after_exec()
                # if valid then add to output queues
                if payload is not None:
                    self.payloads_queue.append(payload)
                if commands is not None:
                    self.commands_queue.append(commands)

                # plugin must be FASTER than main loop
                # cfg_plugin_loop_resolution = MAX(get(_CONFIG, 20), get(MAIN_LOOP))
                sleep(1 / self.cfg_plugin_loop_resolution)

            self._on_close()
        except:
            self.P("Thread exception")

        self.P("Thread ... end")
        return

# DONE


def execute_non_threaded(self):
    self.inputs = self.upstream_data_deque.popleft()
    if self.inputs is not None:
        self._on_data()
    else:
        self._on_idle()

    payload = self.get_payload_after_exec()
    commands = self.get_commands_after_exec()
    # if valid then add to output queues
    if payload is not None:
        self.payloads_queue.append(payload)
    if commands is not None:
        self.commands_queue.append(commands)

# DONE manager stuff

# DONE


def _check_instances(self,):
    # ...
    # at instantiezation
    self.comm_shared_memory['payloads'][instance_hash] = deque()
    self.comm_shared_memory['commands'][instance_hash] = deque()
    plugin.payloads_queue = self.dct_payloads_queues[instance_hash]
    plugin.commands_queue = self.dct_commands_queues[instance_hash]
    # now PluginsManager.comm_shared_memory for the new plugin is available for external .popleft()
    # ...

    plugin = _cls_def(
        log=self.log,
        global_shmem=self.shmem,
        stream_id=stream_name,
        signature=signature,
        default_config=_config_dict,
        upstream_config=upstream_config,
        shared_data=self._shared_data,
        initiator_id=initiator_id,
        session_id=session_id,
        threaded_execution_chain=self._run_on_threads,
    )

    self.P("New `{}` added in the loop.".format(repr(plugin)), color='g')

    self._dct_current_instances[instance_hash] = plugin
    if self._run_on_threads:
        plugin._thread = Thread(target=plugin.plugin_loop, daemon=True)
        plugin._thread.start()

# DONE


def execute_all_plugins(self, dct_plugins_inputs, payload_type=None):
    valid_payload_types = ['INSTANCE', 'STREAM', 'BOX']

    if payload_type is None:
        payload_type = 'STREAM'
    # endif

    if payload_type not in valid_payload_types:
        self.P("ERROR! Payload type {} not valid. We take the default one - {}".format(
            payload_type, valid_payload_types[0]))
        payload_type = valid_payload_types[0]
    # endif

    for instance_hash, inputs in dct_plugins_inputs.items():
        plugin = self.get_subaltern(instance_hash)
        plugin.add_inputs(inputs)
        if not self._run_on_threads:
            plugin.execute_non_threaded()

    # and ze ze zeet
    return

# DONE


def orchestrator_comm_thread(self):
    while not self.__done:
        return_code, status = self.communicate_recv_and_handle()
        payloads = []
        commands = []
        payload_instances = list(self.__comm_shared_memory['payloads'].keys())
        for instance_hash in payload_instances:
            while (self.__comm_shared_memory['payloads'][instance_hash]) > 0:
                payloads.append(
                    self.__comm_shared_memory['payloads'][instance_hash].popleft())

        commands_instances = list(self.__comm_shared_memory['commands'].keys())
        for instance_hash in commands_instances:
            while (self.__comm_shared_memory['commands'][instance_hash]) > 0:
                commands.append(
                    self.__comm_shared_memory['commands'][instance_hash].popleft())

        self.communicate_send(
            payloads=payloads,
            commands=commands,
            status=status
        )  # includes a _maybe_send_heartbeat()
        # orchestrator comm thread must be faster than main loop
        sleep(1 / (self.cfg_main_loop_resolution + DELTA))  # maybe DELTA = 1
    # now force any remaining buffer
