"""FPGA-run orchestration.

Public entry points:
  * `run_simulation_foreground(project, registry)` — foreground exec,
    direct SSH+pty session. (`fslab sim fpga`)
  * `launch_detached(project, registry) -> run_id` — nohup-launch the
    run wrapper, write local stamp, return immediately.
    (`fslab sim fpga --detach`)
  * `monitor_run(project, registry)` — attach to an in-flight detached
    run; pulls results + runs cleanup on wrapper exit.
    (`fslab monitor run`)

The pipeline-agnostic host abstraction (`Host`, `ExternalHost`,
`Ec2LaunchHost`, `HostProvider`, provider registry, `cleanup_remote`)
and the generic monitor primitives this package builds on live in
[fslab.pipeline](../pipeline/).
"""

from .launch import check_no_existing_run, launch_detached
from .monitor_run import monitor_run
from .runconfig import InvalidRunConfig, RunConfig
from .runner import (
    F2Runner,
    RUNNER_CLASS_REGISTRY,
    Runner,
    RunSimulationFailed,
    make_runner,
    make_run_id,
    register_runner_class,
    run_simulation_foreground,
)
from .run_stamp import (
    RemoteInfo,
    RunInfo,
    RunStamp,
    RunStatus,
    read_stamp as read_run_stamp,
    stamp_path_for as run_stamp_path_for,
    staging_path_for as run_staging_path_for,
    wipe_stamp as wipe_run_stamp,
    write_stamp as write_run_stamp,
)

__all__ = [
    # config
    "RunConfig",
    "InvalidRunConfig",
    # runner
    "Runner",
    "F2Runner",
    "RUNNER_CLASS_REGISTRY",
    "register_runner_class",
    "make_runner",
    "make_run_id",
    "RunSimulationFailed",
    "run_simulation_foreground",
    # detached launch
    "launch_detached",
    "check_no_existing_run",
    # monitor
    "monitor_run",
    # stamp
    "RunStamp",
    "RunStatus",
    "RemoteInfo",
    "RunInfo",
    "read_run_stamp",
    "write_run_stamp",
    "wipe_run_stamp",
    "run_stamp_path_for",
    "run_staging_path_for",
]
