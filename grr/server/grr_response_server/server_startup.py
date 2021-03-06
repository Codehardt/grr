#!/usr/bin/env python
"""Server startup routines."""
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import logging
import os
import platform

import prometheus_client

from grr_response_core import config
from grr_response_core.config import contexts
from grr_response_core.lib import communicator
from grr_response_core.lib import config_lib
from grr_response_core.lib import utils
from grr_response_core.lib.local import plugins  # pylint: disable=unused-import
from grr_response_core.lib.parsers import all as all_parsers
from grr_response_core.stats import stats_collector_instance

from grr_response_server import aff4
from grr_response_server import artifact
from grr_response_server import data_store
from grr_response_server import email_alerts
from grr_response_server import ip_resolver
from grr_response_server import prometheus_stats_collector
from grr_response_server import sequential_collection
from grr_response_server import server_logging
from grr_response_server import server_metrics
from grr_response_server import server_plugins  # pylint: disable=unused-import
from grr_response_server import stats_server
from grr_response_server.aff4_objects import aff4_grr
from grr_response_server.aff4_objects import cronjobs
from grr_response_server.aff4_objects import filestore
from grr_response_server.authorization import client_approval_auth
from grr_response_server.blob_stores import registry_init as bs_registry_init
from grr_response_server.check_lib import checks
from grr_response_server.decoders import all as all_decoders
from grr_response_server.gui import api_auth_manager
from grr_response_server.gui import gui_plugins  # pylint: disable=unused-import
from grr_response_server.gui import http_api
from grr_response_server.gui import webauth
from grr_response_server.hunts import results

# pylint: disable=g-import-not-at-top
if platform.system() != "Windows":
  import pwd
# pylint: enable=g-import-not-at-top


def DropPrivileges():
  """Attempt to drop privileges if required."""
  if config.CONFIG["Server.username"]:
    try:
      os.setuid(pwd.getpwnam(config.CONFIG["Server.username"]).pw_uid)
    except (KeyError, OSError):
      logging.exception("Unable to switch to user %s",
                        config.CONFIG["Server.username"])
      raise


@utils.RunOnce  # Make sure we do not reinitialize multiple times.
def Init():
  """Run all required startup routines and initialization hooks."""
  # Set up a temporary syslog handler so we have somewhere to log problems
  # with ConfigInit() which needs to happen before we can start our create our
  # proper logging setup.
  syslog_logger = logging.getLogger("TempLogger")
  if os.path.exists("/dev/log"):
    handler = logging.handlers.SysLogHandler(address="/dev/log")
  else:
    handler = logging.handlers.SysLogHandler()
  syslog_logger.addHandler(handler)

  try:
    config_lib.SetPlatformArchContext()
    config_lib.ParseConfigCommandLine(rename_invalid_writeback=False)
  except config_lib.Error:
    syslog_logger.exception("Died during config initialization")
    raise

  metric_metadata = server_metrics.GetMetadata()
  metric_metadata.extend(communicator.GetMetricMetadata())

  stats_collector = prometheus_stats_collector.PrometheusStatsCollector(
      metric_metadata, registry=prometheus_client.REGISTRY)
  stats_collector_instance.Set(stats_collector)

  server_logging.ServerLoggingStartupInit()

  bs_registry_init.RegisterBlobStores()
  all_decoders.Register()
  all_parsers.Register()

  data_store.InitializeDataStore()

  if data_store.AFF4Enabled():
    aff4.AFF4Init()  # Requires data_store.InitializeDataStore.
    aff4_grr.GRRAFF4Init()  # Requires aff4.AFF4Init.
    filestore.FileStoreInit()  # Requires aff4_grr.GRRAFF4Init.
    results.ResultQueueInit()  # Requires aff4.AFF4Init.
    sequential_collection.StartUpdaterOnce()

  if contexts.ADMIN_UI_CONTEXT in config.CONFIG.context:
    api_auth_manager.InitializeApiAuthManager()

  artifact.LoadArtifactsOnce()  # Requires aff4.AFF4Init.
  checks.LoadChecksFromFilesystemOnce()
  client_approval_auth.InitializeClientApprovalAuthorizationManagerOnce()
  cronjobs.InitializeCronWorkerOnce()  # Requires aff4.AFF4Init.
  email_alerts.InitializeEmailAlerterOnce()
  http_api.InitializeHttpRequestHandlerOnce()
  ip_resolver.IPResolverInitOnce()
  stats_server.InitializeStatsServerOnce()
  webauth.InitializeWebAuthOnce()

  # Exempt config updater from this check because it is the one responsible for
  # setting the variable.
  if not config.CONFIG.ContextApplied("ConfigUpdater Context"):
    if not config.CONFIG.Get("Server.initialized"):
      raise RuntimeError("Config not initialized, run \"grr_config_updater"
                         " initialize\". If the server is already configured,"
                         " add \"Server.initialized: True\" to your config.")
