"""Request-driven admin plugins wait for work instead of polling, also where they are disabled."""
import unittest

from naeural_core.business.default.admin.local_comms_01 import LocalComms01Plugin
from naeural_core.business.default.admin.net_config_monitor import NetConfigMonitorPlugin
from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin
from naeural_core.business.default.web_app.supervisor_fast_api_web_app import SupervisorFastApiWebApp

RUNNING = "_SupervisorFastApiWebApp__supervisor_fastapi_plugin_running"


def _app(running, started, queued=()):
  app = object.__new__(SupervisorFastApiWebApp)
  vars(app).update({
    RUNNING: running, "cfg_idle_loop_wait": 0.25, "_FastApiWebAppPlugin__uvicorn_server_started": started,
    "_incoming_requests": list(queued), "postponed_requests": [],
  })
  return app


class TestIdleWaitOptIns(unittest.TestCase):

  def test_admin_plugins_opt_in_and_other_web_apps_do_not(self):
    for cls in (SupervisorFastApiWebApp, NetConfigMonitorPlugin, LocalComms01Plugin):
      self.assertEqual(cls.CONFIG["IDLE_LOOP_WAIT"], 0.25, cls.__name__)
    # Product web apps run work from process() with no wake source: they keep the fixed rate.
    self.assertEqual(FastApiWebAppPlugin.CONFIG["IDLE_LOOP_WAIT"], 0)

  def test_an_enabled_api_waits_only_once_its_server_is_up_and_nothing_is_queued(self):
    self.assertEqual(_app(running=True, started=False)._idle_loop_wait(), 0)
    self.assertEqual(_app(running=None, started=False)._idle_loop_wait(), 0)
    self.assertEqual(_app(running=True, started=True, queued=[{"id": 1}])._idle_loop_wait(), 0)
    self.assertEqual(_app(running=True, started=True)._idle_loop_wait(), 0.25)

  def test_a_disabled_api_waits_although_its_server_never_starts(self):
    # Non-oracle nodes: six such apps lapped ~27 times a second re-checking the enable state.
    self.assertEqual(_app(running=False, started=False)._idle_loop_wait(), 0.25)


if __name__ == "__main__":
  unittest.main()
