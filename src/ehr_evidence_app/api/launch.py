"""Start the application (Phase 8, decisions D1 and D2).

Starts the API (api.app --serve) and the interface (interface.serve) as
two processes on 127.0.0.1, with a fresh launch token that each receives
in its process environment only; waits until the API answers and then
the interface; prints the interface's address; and stops both together
on Ctrl+C, or when either of them stops. The API loads the model in the
background, the templates answering until it is ready. The processes'
own output is discarded: their routine logs are in the workspace, and
nothing either prints reaches this console.

Each process runs in a process group of its own, so that Ctrl+C reaches
only the launcher, which then asks each to stop (Ctrl+Break), letting the
API stop the model worker, and ends a process that does not.

By default the API serves the invented case set and its scripted model,
reading and writing nothing: a demonstration without data.
--operator-real serves the development case sets with the model, or with
--no-model with the templates only.

Usage:
    python -m ehr_evidence_app.api.launch
    python -m ehr_evidence_app.api.launch --operator-real
    python -m ehr_evidence_app.api.launch --operator-real --no-model
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

import requests

from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import client as clients

API_PORT = 8601
INTERFACE_PORT = 8501
START_SECONDS = 300
STOP_SECONDS = 30


class LaunchError(Exception):
    """An application that cannot be started; carries a short code."""


def port_free(port):
    """Return True when a port of 127.0.0.1 can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def environment(token, api_port, base=None):
    """Return the processes' environment: this one, with the launch token
    and the API's address."""
    found = dict(os.environ if base is None else base)
    found[boundary.TOKEN_VARIABLE] = token
    found[clients.URL_VARIABLE] = "http://127.0.0.1:%d" % api_port
    return found


def commands(api_port, interface_port, real=False, model=True,
             python=None):
    """Return the command lines of the API and the interface."""
    python = python or sys.executable
    api = [python, "-m", "ehr_evidence_app.api.app", "--serve", "--port",
           str(api_port)]
    interface = [python, "-m", "ehr_evidence_app.interface.serve",
                 "--port", str(interface_port)]
    if not real:
        interface.append("--no-log")
    else:
        api.append("--operator-real")
        if not model:
            api.append("--no-model")
    return api, interface


def start(command, env):
    """Start one process in a process group of its own, sharing this
    console so that it can be asked to stop, its output discarded."""
    return subprocess.Popen(
        command, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def stop(process):
    """Ask a process to stop; end it when it does not."""
    if process.poll() is not None:
        return
    try:
        if hasattr(signal, "CTRL_BREAK_EVENT"):
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=STOP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()


def wait_until(url, process, seconds=START_SECONDS):
    """Wait until a URL answers 200, while its process runs."""
    session = requests.Session()
    session.trust_env = False
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LaunchError("process_stopped")
        try:
            if session.get(url, timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise LaunchError("start_timeout")


def launch(api_port=API_PORT, interface_port=INTERFACE_PORT, real=False,
           model=True, say=print):
    """Start both processes and serve until Ctrl+C or until one stops."""
    for port in (api_port, interface_port):
        if not port_free(port):
            raise LaunchError("port_in_use: %d" % port)
    env = environment(boundary.new_token(), api_port)
    api_command, interface_command = commands(api_port, interface_port,
                                              real, model)
    processes = []
    try:
        processes.append(start(api_command, env))
        wait_until("http://127.0.0.1:%d/health" % api_port, processes[0])
        say("The API is serving %s on 127.0.0.1:%d%s." % (
            "the development case sets" if real else
            "the invented case set", api_port,
            "" if not real else "; the model is loading" if model
            else ", with the templates only"))
        processes.append(start(interface_command, env))
        wait_until("http://127.0.0.1:%d/_stcore/health" % interface_port,
                   processes[1])
        say("Open the interface at http://127.0.0.1:%d/" % interface_port)
        say("Press Ctrl+C to stop.")
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise LaunchError("process_stopped")
    finally:
        for process in reversed(processes):
            stop(process)


def main(argv=None):
    """Start the application."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-port", type=int, default=API_PORT)
    parser.add_argument("--port", type=int, default=INTERFACE_PORT,
                        help="the interface's port")
    parser.add_argument("--operator-real", action="store_true",
                        help="serve the development case sets, with the "
                             "model")
    parser.add_argument("--no-model", action="store_true",
                        help="answer the development case sets with the "
                             "templates only")
    args = parser.parse_args(argv)
    if not all(1024 <= port <= 65535 for port in (args.api_port, args.port)) \
            or args.api_port == args.port:
        print("FAILED: the ports are two numbers from 1024 to 65535")
        return 2
    if args.no_model and not args.operator_real:
        print("FAILED: --no-model goes with --operator-real")
        return 2
    try:
        launch(args.api_port, args.port, real=args.operator_real,
               model=not args.no_model)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except LaunchError as error:
        print("FAILED: %s" % error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
