#!/usr/bin/env python3
"""Manage only the services and persistent data in this standalone folder."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/manage.py"
DATA = ROOT / "data"
LOGS = DATA / "logs"
PID_FILE = DATA / "run.pid"
STATE_FILE = DATA / "state.json"
DATABASE_CONFIG = DATA / "database.json"
PGDATA = DATA / "pgdata"
PYTHON = ROOT / "backend/.venv/bin/python"
PORT = 3120
URL = f"http://127.0.0.1:{PORT}"
REDACTIONS = []


def redact(value):
    value = str(value)
    for secret in REDACTIONS:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def log(value):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {redact(value)}", flush=True)


def prepare_directories():
    os.umask(0o077)
    for directory in (DATA, LOGS, DATA / "attachments"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)


def write_json(path, value):
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextmanager
def lock(name, *, blocking=True):
    with (DATA / name).open("a+") as handle:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle, flags)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def command_for(pid):
    if type(pid) is not int or pid <= 1:
        return ""
    return subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="],
                          capture_output=True, text=True, check=False).stdout.strip()


def owner_is_alive(state):
    token = state.get("token", "")
    command = command_for(state.get("pid"))
    return bool(token and str(SCRIPT) in command and "_serve" in command and token in command)


def stack_is_alive(state):
    command = command_for(state.get("stack_pid"))
    return str(ROOT / "frontend/scripts/run-stack.mjs") in command


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_free(port):
    try:
        with socket.socket() as sock:
            # Match Node/PostgreSQL: recently closed connections in TIME_WAIT
            # must not be mistaken for another running listener on restart.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def load_environment():
    from dotenv import dotenv_values

    path = ROOT / "config/runtime.env"
    if not path.is_file():
        raise RuntimeError("缺少 config/runtime.env，请补齐成品目录内的私有配置。")
    path.chmod(0o600)
    values = dotenv_values(path, interpolate=False)
    for name in ("OPENROUTER_API_KEY", "EXA_API_KEY"):
        value = (values.get(name) or "").strip()
        if value:
            REDACTIONS.append(value)
        if not value or any(c in value for c in "\r\n"):
            raise RuntimeError(f"config/runtime.env 缺少有效的 {name}")
        values[name] = value
    env = os.environ.copy()
    # Runtime settings are private to this copy; inherited demo/remote settings
    # must never turn the finished application into a scripted frontend.
    for name in list(env):
        if name.startswith("WELLIO_") or name.startswith("VITE_WELLIO_"):
            env.pop(name)
    env.pop("DATABASE_URL", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("VIRTUAL_ENV", None)
    env.update({name: values[name] for name in ("OPENROUTER_API_KEY", "EXA_API_KEY")})
    env["WELLIO_AI_MODEL"] = (values.get("WELLIO_AI_MODEL") or "deepseek/deepseek-v4.1-flash").strip()
    env["PATH"] = os.pathsep.join((str(ROOT / "runtime/bin"), str(PYTHON.parent),
                                   "/opt/homebrew/bin", env.get("PATH", "")))
    node = next((str(path) for path in (ROOT / "runtime/bin/node", Path("/opt/homebrew/bin/node"))
                 if path.is_file() and os.access(path, os.X_OK)), None)
    node = node or shutil.which("node", path=env["PATH"])
    if not node:
        raise RuntimeError("找不到 Node.js。请检查 runtime/bin/node 或本机 PATH。")
    pg = Path(os.environ.get("WELLIO_PG_BIN") or values.get("WELLIO_PG_BIN")
              or "/opt/homebrew/opt/postgresql@18/bin").expanduser().resolve()
    for binary in ("initdb", "pg_ctl", "postgres"):
        if not os.access(pg / binary, os.X_OK):
            raise RuntimeError(f"找不到 PostgreSQL 程序：{pg / binary}；可设置 WELLIO_PG_BIN。")
    for path in (PYTHON, ROOT / "frontend/.output/server/index.mjs",
                 ROOT / "frontend/scripts/run-stack.mjs", ROOT / "backend/agent-runtime/dist/server.js"):
        if not path.exists():
            raise RuntimeError(f"成品文件不完整：{path.relative_to(ROOT)}")
    env.update(WELLIO_BACKEND_DIR=str(ROOT / "backend"), WELLIO_PYTHON=str(PYTHON),
               WELLIO_ATTACHMENTS_PATH=str(DATA / "attachments"), WELLIO_COOKIE_SECURE="0",
               WELLIO_PUBLIC_ORIGIN=f"{URL},http://localhost:{PORT}", PORT=str(PORT), NITRO_PORT=str(PORT),
               HOST="127.0.0.1", VITE_WELLIO_MODE="live", VITE_WELLIO_PREVIEW="0",
               COPILOTKIT_TELEMETRY_DISABLED="true", PYTHONUNBUFFERED="1")
    return env, str(node), pg


def database_settings():
    if DATABASE_CONFIG.exists():
        config = read_json(DATABASE_CONFIG)
        if (type(config.get("port")) is not int or not 1024 <= config["port"] <= 65535
                or not re.fullmatch(r"[a-f0-9]{48}", config.get("password", ""))):
            raise RuntimeError("data/database.json 无效；为保护持久数据，启动已停止。")
    else:
        if PGDATA.exists() and any(PGDATA.iterdir()):
            raise RuntimeError("已有 PostgreSQL 数据但缺少 data/database.json；请恢复配套配置。")
        config = {"port": free_port(), "password": secrets.token_hex(24)}
        write_json(DATABASE_CONFIG, config)
    DATABASE_CONFIG.chmod(0o600)
    REDACTIONS.append(config["password"])
    return config


def run_logged(command, *, env=None, timeout=120, check=True):
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
    for line in result.stdout.splitlines():
        log(line)
    if check and result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} 执行失败，退出码 {result.returncode}。")
    return result


def pg_running(pg):
    if not (PGDATA / "PG_VERSION").exists():
        return False
    return subprocess.run([str(pg / "pg_ctl"), "-D", str(PGDATA), "status"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


def stop_postgres(pg):
    if pg_running(pg):
        log("正在停止本成品目录的 PostgreSQL；数据保留。")
        run_logged([str(pg / "pg_ctl"), "-D", str(PGDATA), "-m", "fast", "-w", "-t", "30", "stop"], timeout=40)


def initialize_postgres(pg, config, env):
    if not (PGDATA / "PG_VERSION").exists():
        if PGDATA.exists() and any(PGDATA.iterdir()):
            raise RuntimeError("data/pgdata 包含未完成的初始化数据，请检查日志后处理。")
        password_file = DATA / ".init-password"
        password_file.write_text(config["password"], encoding="utf-8")
        password_file.chmod(0o600)
        try:
            log("首次启动：初始化独立 PostgreSQL 数据目录。")
            run_logged([str(pg / "initdb"), "-D", str(PGDATA), "-U", "wellio_demo",
                        "--auth-local=trust", "--auth-host=scram-sha-256", "--pwfile", str(password_file),
                        "--encoding=UTF8", "--locale=C"], env=env)
        finally:
            password_file.unlink(missing_ok=True)
    if not pg_running(pg):
        if not port_is_free(config["port"]):
            config["port"] = free_port()
            write_json(DATABASE_CONFIG, config)
        run_logged([str(pg / "pg_ctl"), "-D", str(PGDATA), "-l", str(LOGS / "postgres.log"),
                    "-o", f"-h 127.0.0.1 -p {config['port']} -c unix_socket_directories=''",
                    "-w", "-t", "60", "start"], env=env, timeout=70)
    env["DATABASE_URL"] = f"postgresql://wellio_demo:{config['password']}@127.0.0.1:{config['port']}/postgres"


def health_ready():
    # Startup readiness only. User-facing features are accepted in the browser.
    try:
        with urlopen(URL + "/api/copilotkit/info", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def open_page():
    subprocess.run(["/usr/bin/open", URL], check=True)


def relay_output(child):
    for line in child.stdout:
        log(line.rstrip())


def terminate_stack(child):
    if child is None:
        return
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=12)
        except subprocess.TimeoutExpired:
            pass
    # run-stack owns a fresh group; remaining children can only belong to this
    # launch, including any child left behind after its Node supervisor exits.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def serve(token):
    with lock("run.lock", blocking=False):
        child = None
        pg = None
        state = {"pid": os.getpid(), "token": token, "root": str(ROOT), "url": URL, "status": "starting"}
        write_json(PID_FILE, state)
        write_json(STATE_FILE, state)

        def shutdown(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            log("启动 Wellio 独立成品。")
            if not port_is_free(PORT):
                raise RuntimeError(f"端口 {PORT} 已被其他程序占用；没有关闭该程序。")
            env, node, pg = load_environment()
            state["pg_bin"] = str(pg)
            write_json(PID_FILE, state)
            config = database_settings()
            initialize_postgres(pg, config, env)
            log("准备随包知识库。此步骤使用本地缓存，不调用模型。")
            run_logged([str(PYTHON), str(ROOT / "scripts/initialize_knowledge.py")], env=env)
            child = subprocess.Popen([node, str(ROOT / "frontend/scripts/run-stack.mjs"), "start"],
                                     cwd=ROOT / "frontend", env=env, start_new_session=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            state["stack_pid"] = child.pid
            write_json(PID_FILE, state)
            output_thread = threading.Thread(target=relay_output, args=(child,), daemon=True)
            output_thread.start()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"应用启动提前退出，退出码 {child.returncode}；请查看日志。")
                if health_ready():
                    state["status"] = "ready"
                    write_json(STATE_FILE, state)
                    log(f"启动就绪：{URL}")
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("应用在 120 秒内未就绪；请查看 data/logs/launcher.log。")
            code = child.wait()
            if code:
                raise RuntimeError(f"应用服务已退出，退出码 {code}。")
        except SystemExit:
            state["status"] = "stopped"
        except Exception as error:
            state["status"] = "failed"
            state["error"] = redact(error)
            log(f"启动或运行失败：{error}")
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                terminate_stack(child)
            except Exception as error:
                log(f"清理应用服务时发生错误：{error}")
            if pg is not None:
                try:
                    stop_postgres(pg)
                except Exception as error:
                    state["status"] = "failed"
                    state["error"] = redact(error)
                    log(f"数据库停止未完成：{error}")
            if state["status"] not in ("failed", "stopped"):
                state["status"] = "stopped"
            write_json(STATE_FILE, state)
            if read_json(PID_FILE).get("token") == token:
                PID_FILE.unlink(missing_ok=True)
            log("本次启动进程已结束。")
        return 1 if state["status"] == "failed" else 0


def start(no_open):
    with lock("command.lock"):
        state = read_json(PID_FILE)
        if owner_is_alive(state):
            token = state["token"]
            log("本成品已在运行或启动中，等待就绪。")
        else:
            if stack_is_alive(state):
                raise RuntimeError("检测到本成品上次留下的应用服务，请先运行停止.command。")
            token = secrets.token_hex(16)
            with (LOGS / "launcher.log").open("a", encoding="utf-8") as logfile:
                subprocess.Popen([str(PYTHON), str(SCRIPT), "_serve", "--token", token],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=logfile,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            state = read_json(STATE_FILE)
            owner = read_json(PID_FILE)
            if state.get("token") == token:
                if state.get("status") == "ready" and owner_is_alive(owner):
                    log(f"Wellio 已就绪：{URL}")
                    log(f"运行日志：{LOGS / 'launcher.log'}")
                    if not no_open:
                        open_page()
                    return 0
                if state.get("status") in ("failed", "stopped"):
                    raise RuntimeError(state.get("error") or "启动已停止，请查看 data/logs/launcher.log。")
            time.sleep(0.3)
        raise RuntimeError("等待启动超时，请查看 data/logs/launcher.log，或运行停止.command 后重试。")


def stop():
    with lock("command.lock"):
        state = read_json(PID_FILE)
        if owner_is_alive(state):
            log("正在停止本成品的应用服务与数据库……")
            os.kill(state["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 60
            while owner_is_alive(state) and time.monotonic() < deadline:
                time.sleep(0.2)
            if owner_is_alive(state):
                raise RuntimeError("停止仍未完成，请查看日志；未强杀可能正在清理的主进程。")
        # Recover a stack left behind by an interrupted launcher. The verified
        # command and process group are both scoped to this exact folder.
        if stack_is_alive(state):
            stack_pid = state["stack_pid"]
            if os.getpgid(stack_pid) == stack_pid:
                os.killpg(stack_pid, signal.SIGTERM)
                deadline = time.monotonic() + 12
                while stack_is_alive(state) and time.monotonic() < deadline:
                    time.sleep(0.2)
                if stack_is_alive(state):
                    os.killpg(stack_pid, signal.SIGKILL)
        pg = Path(state.get("pg_bin") or read_json(STATE_FILE).get("pg_bin")
                  or os.environ.get("WELLIO_PG_BIN") or "/opt/homebrew/opt/postgresql@18/bin")
        if (PGDATA / "PG_VERSION").exists():
            if not os.access(pg / "pg_ctl", os.X_OK):
                raise RuntimeError(f"找不到 {pg / 'pg_ctl'}，无法确认数据库已停止。")
            stop_postgres(pg)
        PID_FILE.unlink(missing_ok=True)
        final_state = read_json(STATE_FILE)
        final_state["status"] = "stopped"
        write_json(STATE_FILE, final_state)
        log("Wellio 已停止；data/ 内的记录、附件和数据库均已保留。")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Wellio 独立成品启动管理")
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("start", help="后台启动服务并打开页面")
    launch.add_argument("--no-open", action="store_true", help="启动后不打开浏览器")
    sub.add_parser("stop", help="只停止本目录的服务，保留数据")
    sub.add_parser("open", help="打开已启动的本地页面")
    sub.add_parser("status", help="显示本目录的启动状态")
    worker = sub.add_parser("_serve", help=argparse.SUPPRESS)
    worker.add_argument("--token", required=True)
    args = parser.parse_args()
    prepare_directories()
    if args.command == "_serve":
        return serve(args.token)
    if args.command == "start":
        return start(args.no_open)
    if args.command == "stop":
        return stop()
    state = read_json(PID_FILE)
    alive = owner_is_alive(state)
    if args.command == "open":
        if not alive or read_json(STATE_FILE).get("status") != "ready":
            raise RuntimeError("Wellio 尚未启动，请先双击启动.command。")
        open_page()
    else:
        log(f"状态：{read_json(STATE_FILE).get('status', 'stopped') if alive else 'stopped'}；地址：{URL}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log(f"错误：{error}")
        raise SystemExit(1)
