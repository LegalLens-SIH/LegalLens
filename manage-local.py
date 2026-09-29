"""Start and manage the local LegalLense development services on Windows."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "backend" / ".venv311" / "Scripts" / "python.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SERVICES = {
    "backend": {
        "command": [
            str(PYTHON),
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        "cwd": ROOT,
        "markers": ("-m", "uvicorn", "backend.main:app", "--port", "8000"),
        "pid_file": ROOT / "server_run.pid",
        "stdout": ROOT / "server_run.out.log",
        "stderr": ROOT / "server_run.err.log",
    },
    "frontend": {
        "command": [
            str(PYTHON),
            "-m",
            "http.server",
            "5500",
            "--bind",
            "127.0.0.1",
        ],
        "cwd": ROOT / "frontend",
        "markers": ("-m", "http.server", "5500", "--bind", "127.0.0.1"),
        "pid_file": ROOT / "frontend_run.pid",
        "stdout": ROOT / "frontend_run.out.log",
        "stderr": ROOT / "frontend_run.err.log",
    },
}


def _matches_service(process, service):
    config = SERVICES[service]
    try:
        command = process.cmdline()
        cwd = process.cwd()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    return (
        all(marker in command for marker in config["markers"])
        and os.path.normcase(os.path.abspath(cwd))
        == os.path.normcase(str(config["cwd"]))
    )


def _find_process(service):
    pid_file = SERVICES[service]["pid_file"]
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        pid = None

    if pid is not None:
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            process = None
        if process is not None and _matches_service(process, service):
            return process

    for process in psutil.process_iter():
        if _matches_service(process, service):
            return process
    return None


def _write_pid(service, pid):
    SERVICES[service]["pid_file"].write_text(str(pid), encoding="ascii")


def _start_one(service):
    config = SERVICES[service]
    existing = _find_process(service)
    if existing is not None:
        _write_pid(service, existing.pid)
        print(f"{service} already running (PID {existing.pid})")
        return False

    with config["stdout"].open("ab") as stdout, config["stderr"].open("ab") as stderr:
        process = subprocess.Popen(
            config["command"],
            cwd=config["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=NO_WINDOW,
        )

    time.sleep(0.25)
    if process.poll() is not None:
        raise RuntimeError(
            f"{service} exited with code {process.returncode}; "
            f"see {config['stderr'].name}"
        )

    _write_pid(service, process.pid)
    print(f"Started {service} (PID {process.pid})")
    return True


def start():
    started = []
    try:
        for service in SERVICES:
            if _start_one(service):
                started.append(service)
    except (OSError, RuntimeError) as error:
        for service in reversed(started):
            _stop_one(service)
        raise error
    print("Frontend: http://localhost:5500")
    print("Backend:  http://localhost:8000")


def _stop_one(service):
    config = SERVICES[service]
    process = _find_process(service)
    if process is None:
        config["pid_file"].unlink(missing_ok=True)
        print(f"{service} is not running")
        return

    descendants = process.children(recursive=True)
    for child in descendants:
        try:
            child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.terminate()
        _, alive = psutil.wait_procs([process, *descendants], timeout=10)
        for remaining in alive:
            remaining.kill()
        if alive:
            psutil.wait_procs(alive, timeout=5)
    except psutil.NoSuchProcess:
        pass

    config["pid_file"].unlink(missing_ok=True)
    print(f"Stopped {service} (PID {process.pid})")


def stop():
    for service in reversed(SERVICES):
        _stop_one(service)


def status():
    for service in SERVICES:
        process = _find_process(service)
        if process is None:
            SERVICES[service]["pid_file"].unlink(missing_ok=True)
            print(f"{service}: stopped")
        else:
            _write_pid(service, process.pid)
            print(f"{service}: running (PID {process.pid})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    action = parser.parse_args().action

    if action == "start":
        start()
    elif action == "stop":
        stop()
    elif action == "restart":
        stop()
        start()
    else:
        status()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"Startup manager error: {error}", file=sys.stderr)
        sys.exit(1)