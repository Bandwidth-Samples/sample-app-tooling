#!/usr/bin/env python3
"""Black-box contract runner: boots a sample app and checks it against a contract YAML."""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

TOOLING_ROOT = Path(__file__).resolve().parent.parent
FAILURE_EXIT_TIMEOUT = 15


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_env(contract, port, unset=None, override=None):
    # real creds in the caller's shell must not reach the app under test
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BW_", "CLIENT_")) and k != "LOCAL_PORT"}
    for key, value in contract["env"].items():
        env[key] = value.replace("{port}", str(port))
    for key in (unset,) if unset else ():
        env.pop(key, None)
    env.update(override or {})
    return env


def start_app(command, app_dir, env, log):
    return subprocess.Popen(
        command, shell=True, cwd=app_dir, env=env,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def stop_app(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    else:
        # the shell may have exited while a child still holds the port
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def wait_for_port(proc, port, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def send(port, request, fixtures_dir):
    body = None
    headers = {}
    if "fixture" in request:
        body = (fixtures_dir / request["fixture"]).read_bytes()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{request['path']}",
        data=body, method=request["method"], headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def same_value(got, want):
    # SDKs disagree on number formatting, e.g. Java prints duration="30.0"
    try:
        return got == want or float(got) == float(want)
    except ValueError:
        return False


def check_children(node, expected, label):
    errors = []
    actual = list(node)
    if len(actual) != len(expected):
        errors.append(f"{label} has {len(actual)} children, expected {len(expected)}")
    for i, (child, want) in enumerate(zip(actual, expected)):
        if child.tag != want["tag"]:
            errors.append(f"{label} child {i} is <{child.tag}>, expected <{want['tag']}>")
            continue
        text = (child.text or "").strip()
        if "text" in want and text != want["text"]:
            errors.append(f"<{child.tag}> text is {text!r}, expected {want['text']!r}")
        if "text_ends_with" in want and not text.endswith(want["text_ends_with"]):
            errors.append(f"<{child.tag}> text {text!r} doesn't end with {want['text_ends_with']!r}")
        for name, value in want.get("attrs", {}).items():
            suffix = name.endswith("_ends_with")
            attr = name.removesuffix("_ends_with")
            got = child.get(attr)
            if got is None:
                errors.append(f"<{child.tag}> is missing attribute {attr}")
            elif suffix and not got.endswith(value):
                errors.append(f"<{child.tag}> {attr}={got!r} doesn't end with {value!r}")
            elif not suffix and not same_value(got, value):
                errors.append(f"<{child.tag}> {attr}={got!r}, expected {value!r}")
        if "children" in want:
            errors += check_children(child, want["children"], f"<{child.tag}>")
    return errors


def check_bxml(raw, expected):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [f"body is not valid XML: {e}"]
    errors = []
    if root.tag != expected["root"]:
        errors.append(f"root is <{root.tag}>, expected <{expected['root']}>")
    return errors + check_children(root, expected["children"], f"<{root.tag}>")


def check_case(case, port, fixtures_dir):
    status, content_type, body = send(port, case["request"], fixtures_dir)
    expect = case["expect"]
    errors = []
    if status != expect["status"]:
        errors.append(f"status {status}, expected {expect['status']}")
    if "content_type" in expect:
        media_type = content_type.split(";")[0].strip()
        if media_type != expect["content_type"]:
            errors.append(f"content-type {media_type!r}, expected {expect['content_type']!r}")
    if "bxml" in expect:
        errors += check_bxml(body, expect["bxml"])
    if "body_equals_file" in expect:
        want = (fixtures_dir / expect["body_equals_file"]).read_bytes()
        if body != want:
            errors.append(f"body differs from {expect['body_equals_file']}")
    if "json_equals" in expect:
        try:
            actual = json.loads(body)
        except ValueError:
            errors.append("body is not valid JSON")
        else:
            if actual != expect["json_equals"]:
                errors.append(f"body is {actual!r}, expected {expect['json_equals']!r}")
    return errors


def run_startup_failure(case, contract, args, log):
    port = free_port()
    env = build_env(contract, port, unset=case.get("unset"), override=case.get("set"))
    proc = start_app(args.start_command, args.app_dir, env, log)
    try:
        proc.wait(timeout=FAILURE_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return [f"still running after {FAILURE_EXIT_TIMEOUT}s, expected a non-zero exit"]
    finally:
        stop_app(proc)
    return [] if proc.returncode != 0 else ["exited 0, expected non-zero"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--app-dir", default=Path("."), type=Path)
    parser.add_argument("--start-command", required=True)
    parser.add_argument("--fixtures-root", default=TOOLING_ROOT, type=Path,
                        help="directory the contract's fixtures_dir is relative to")
    parser.add_argument("--skip", action="append", default=[], help="case id to skip")
    parser.add_argument("--log-file", type=Path, help="where to write the app's output")
    args = parser.parse_args()
    args.app_dir = args.app_dir.resolve()

    contract = yaml.safe_load(args.contract.read_text())
    fixtures_dir = args.fixtures_root / contract["fixtures_dir"]
    log_path = args.log_file or Path(tempfile.gettempdir()) / "contract-app.log"
    results = []

    with open(log_path, "w") as log:
        port = free_port()
        proc = start_app(args.start_command, args.app_dir, build_env(contract, port), log)
        try:
            booted = wait_for_port(proc, port, contract["startup"]["ready_timeout_seconds"])
            results.append(("T1", "boots with all env vars", [] if booted else ["port never opened"]))
            if booted:
                for case in contract["cases"]:
                    if case.get("skip") or case["id"] in args.skip:
                        results.append((case["id"], case["name"], None))
                        continue
                    results.append((case["id"], case["name"],
                                    check_case(case, port, fixtures_dir)))
        finally:
            stop_app(proc)

        for case in contract["startup"]["failure_cases"]:
            # a crashing app would "pass" every fail-fast case
            if case["id"] in args.skip or not booted:
                results.append((case["id"], "startup failure", None))
                continue
            results.append((case["id"], f"bad env ({case.get('unset') or case.get('set')}) fails fast",
                            run_startup_failure(case, contract, args, log)))

    failed = 0
    for case_id, name, errors in results:
        if errors is None:
            print(f"SKIP  {case_id}  {name}")
        elif errors:
            failed += 1
            print(f"FAIL  {case_id}  {name}")
            for e in errors:
                print(f"        {e}")
        else:
            print(f"PASS  {case_id}  {name}")
    print(f"\n{sum(1 for r in results if r[2] == [])} passed, {failed} failed, "
          f"{sum(1 for r in results if r[2] is None)} skipped (app log: {log_path})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
