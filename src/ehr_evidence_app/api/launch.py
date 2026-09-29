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
only the launcher. The launcher holds each process's standard input open;
a process stops, gracefully, when that closes: when the launcher asks it
to, or when the launcher ends in any way, so that no process outlives it.
A process that does not stop in time is ended.

By default the API serves the invented case set and its scripted model,
reading and writing nothing: a demonstration without data.
--operator-real serves the development case sets with the model, or with
--no-model with the templates only.

--share also shares the interface through the Cloudflare tunnel
(interface.share): the launcher checks the secret link's record, the
tunnel's token file and cloudflared, gives the interface the public host
and the key's digest, starts cloudflared with the token file, and checks
that the public address answers as the gate answers a visit without the
key. Every process is in a Windows job that ends it when the launcher
ends, cloudflared and the model worker included.

Usage:
    python -m ehr_evidence_app.api.launch
    python -m ehr_evidence_app.api.launch --operator-real
    python -m ehr_evidence_app.api.launch --operator-real --no-model
    python -m ehr_evidence_app.api.launch --operator-real --share
"""

from __future__ import annotations

import argparse
import functools
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import requests

from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import serve
from ehr_evidence_app.interface import share as sharing

API_PORT = 8601
INTERFACE_PORT = 8501
START_SECONDS = 300
STOP_SECONDS = 30
WATCH_VARIABLE = "EHR_EXIT_WITH_LAUNCHER"
# How often a process the launcher started looks for the launcher's end
# of its standard input to close.
WATCH_SECONDS = 0.25
say = functools.partial(print, flush=True)


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
    """Return the processes' environment: this one, with the launch token,
    the API's address and the instruction to stop with the launcher."""
    found = dict(os.environ if base is None else base)
    found[boundary.TOKEN_VARIABLE] = token
    found[clients.URL_VARIABLE] = "http://127.0.0.1:%d" % api_port
    found[WATCH_VARIABLE] = "1"
    return found


def pipe_watcher(handle):
    """Return a function that says whether the other end of a Windows
    pipe is still open, without reading from it or waiting; None when the
    handle is not a pipe."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.PeekNamedPipe.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    # FILE_TYPE_PIPE
    if kernel32.GetFileType(handle) != 3:
        return None
    waiting = wintypes.DWORD()

    def still_open():
        """Return False once the pipe is broken or closed."""
        return bool(kernel32.PeekNamedPipe(handle, None, 0, None,
                                           ctypes.byref(waiting), None))
    return still_open


def exit_with_launcher(stream=None, environ=None, seconds=WATCH_SECONDS):
    """In a process the launcher started, stop gracefully when the
    launcher's end of the standard input closes; return the watching
    thread, or None in any other process.

    On Windows the pipe is polled, never read. A read left waiting on it
    would hold the pipe: Windows queues every other use of it behind that
    read, a native library's start-up included, so that the first library
    loaded afterwards (NumPy's, for one) would stall the process for as
    long as the launcher ran."""
    environ = os.environ if environ is None else environ
    if environ.get(WATCH_VARIABLE) != "1":
        return None
    stream = sys.stdin if stream is None else stream
    if stream is None:
        return None
    if os.name == "nt":
        import msvcrt
        try:
            still_open = pipe_watcher(msvcrt.get_osfhandle(stream.fileno()))
        except (OSError, ValueError):
            still_open = None
        if still_open is None:
            return None

        def wait():
            """Poll until the launcher lets go."""
            while still_open():
                time.sleep(seconds)
    else:
        def wait():
            """Read until the launcher lets go."""
            try:
                while stream.read(1024):
                    pass
            except (OSError, ValueError):
                pass

    def watch():
        """Wait for the launcher to let go, then ask this process to
        stop."""
        wait()
        signal.raise_signal(getattr(signal, "SIGBREAK", signal.SIGTERM))

    thread = threading.Thread(target=watch, name="launcher-watch",
                              daemon=True)
    thread.start()
    return thread


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


class Job:
    """A Windows job whose processes end when the launcher ends, however
    it ends, their children included; elsewhere, or where a job cannot be
    made, it holds nothing."""

    def __init__(self):
        """Make the job, set to end its processes when it closes."""
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            """JOBOBJECT_BASIC_LIMIT_INFORMATION."""

            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            """IO_COUNTERS."""

            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            """JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

            _fields_ = [("BasicLimitInformation", Basic),
                        ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p,
                                              wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE,
                                                      wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateJobObjectW(None, None)
        info = Extended()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, set through
        # JobObjectExtendedLimitInformation (9).
        info.BasicLimitInformation.LimitFlags = 0x2000
        if handle and kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.kernel32, self.handle = kernel32, handle
        elif handle:
            kernel32.CloseHandle(handle)

    def add(self, process):
        """Put a process in the job; return False when it cannot be."""
        if self.handle is None:
            return False
        return bool(self.kernel32.AssignProcessToJobObject(
            self.handle, int(process._handle)))

    def close(self):
        """Close the job, ending any process still in it."""
        if self.handle is not None:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def start(command, env):
    """Start one process in a process group of its own, its standard
    input held open by the launcher, its output discarded."""
    return subprocess.Popen(
        command, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def stop(process, seconds=STOP_SECONDS):
    """Ask a process to stop by closing its standard input; end it when
    it does not stop in time."""
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
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


def confirm_shared(host, seconds=60):
    """Return True once the public address answers through the tunnel as
    the gate answers a visit without the key: not found, and marked by
    the gate."""
    session = requests.Session()
    session.trust_env = False
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            reply = session.get("https://%s/" % host, timeout=10,
                                allow_redirects=False)
            if reply.status_code == 404 and reply.headers.get(
                    "x-frame-options") == "DENY":
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def tunnel_environment(base=None):
    """Return cloudflared's environment: this one, without the launch
    token or anything else the application's processes are given."""
    found = dict(os.environ if base is None else base)
    for name in (boundary.TOKEN_VARIABLE, clients.URL_VARIABLE,
                 WATCH_VARIABLE, serve.SHARE_HOST_VARIABLE,
                 serve.SHARE_DIGEST_VARIABLE):
        found.pop(name, None)
    return found


def launch(api_port=API_PORT, interface_port=INTERFACE_PORT, real=False,
           model=True, share=False, cloudflared=sharing.CLOUDFLARED,
           directory=sharing.SHARING_DIR, say=say):
    """Start the processes and serve until Ctrl+C or until one stops."""
    for port in (api_port, interface_port):
        if not port_free(port):
            raise LaunchError("port_in_use: %d" % port)
    env = environment(boundary.new_token(), api_port)
    shared = None
    if share:
        found = sharing.problems(directory, cloudflared)
        if found:
            raise LaunchError(found[0])
        shared = sharing.read_link(directory)
        env[serve.SHARE_HOST_VARIABLE] = shared.host
        env[serve.SHARE_DIGEST_VARIABLE] = shared.key_sha256
    api_command, interface_command = commands(api_port, interface_port,
                                              real, model)
    job, processes, tunnel = Job(), [], []

    def begin(command, environ):
        """Start a process in the job."""
        process = start(command, environ)
        job.add(process)
        processes.append(process)
        return process

    try:
        wait_until("http://127.0.0.1:%d/health" % api_port,
                   begin(api_command, env))
        say("The API is serving %s on 127.0.0.1:%d%s." % (
            "the development case sets" if real else
            "the invented case set", api_port,
            "" if not real else "; the model is loading" if model
            else ", with the templates only"))
        wait_until("http://127.0.0.1:%d/health" % interface_port,
                   begin(interface_command, env))
        say("Open the interface at http://127.0.0.1:%d/" % interface_port)
        if shared:
            tunnel.append(begin(sharing.tunnel_command(cloudflared, directory),
                                tunnel_environment()))
            if confirm_shared(shared.host):
                say("Shared through the tunnel at https://%s/: only the "
                    "secret link opens it." % shared.host)
            else:
                say("The tunnel has not answered yet; check it in "
                    "Cloudflare's dashboard.")
        say("Press Ctrl+C to stop.")
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise LaunchError("process_stopped")
    finally:
        # The tunnel first, so that nothing is reachable while the rest
        # stops; cloudflared does not watch its input, so it is ended.
        for process in tunnel:
            stop(process, seconds=5)
        for process in reversed(processes):
            if process not in tunnel:
                stop(process)
        job.close()


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
    parser.add_argument("--share", action="store_true",
                        help="also share the interface through the tunnel, "
                             "for the secret link only")
    parser.add_argument("--cloudflared", default=sharing.CLOUDFLARED,
                        help="the path of cloudflared.exe")
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
               model=not args.no_model, share=args.share,
               cloudflared=args.cloudflared)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except LaunchError as error:
        print("FAILED: %s" % error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
