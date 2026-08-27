"""G1 — one complete three-state repetition (HEALTHY / FAULTED / RESTORED), driver (ii).

Protocol: SREMut/experiments/PROTOCOL_G1.md
  R1  60 s between inject_fault() returning and the treatment oracle
  R2  >= 10 complete wrk2 rounds measured per state, in this run
  R3  pre-registered interpretation rule (applied at analysis time)

Every oracle call is wrapped in the exact try/except of conductor.py:269-271.
Raw verdict dicts are recorded as returned, never coerced.

Run under SREGym/.venv/bin/python with cwd=SREGym. Writes nothing into SREGym/.
"""

from __future__ import annotations

import argparse, hashlib, json, os, re, signal, subprocess, sys, time, traceback
from datetime import UTC, datetime
from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig

PINNED_KUBECTL = "/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
CONTEXT   = "kind-kind"
NAMESPACE = "social-network"
PROBLEM_ID = "missing_service_social_network"
EXPERIMENTS = Path("/home/sakibbuet2k19/sremut/SREMut/experiments")
WORKER = Path("/home/sakibbuet2k19/sremut/SREMut/src/sremut/original_oracle_worker.py")
STOCK_PY = Path("/home/sakibbuet2k19/sremut/SREGym/.venv/bin/python")
SREGYM_ROOT = Path("/home/sakibbuet2k19/sremut/SREGym")

NULL_AGENT_EPISODE_SECONDS = 60      # R1
MIN_ROUNDS_PER_STATE       = 10      # R2
ROUND_POLL_INTERVAL        = 10
ROUND_POLL_TIMEOUT         = 600


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def say(m: str) -> None:
    print(f"[{utc_now()}] {m}", flush=True)


def k(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PINNED_KUBECTL, "--context", CONTEXT, *args],
                          capture_output=True, text=True)


# ----------------------------------------------------------------- wrk2 rounds
TS_RE = re.compile(r'^(\S+Z)\s(.*)$')


def wrk2_rounds() -> list[dict]:
    """Merged, timestamp-ordered complete rounds across every wrk2 pod. READ-ONLY."""
    pods = k("get", "pods", "-n", NAMESPACE, "-l", "job-name=wrk2-job",
             "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}").stdout.split()
    lines: list[tuple[str, str]] = []
    for p in pods:
        out = k("logs", p, "-n", NAMESPACE, "--timestamps").stdout
        for ln in out.splitlines():
            m = TS_RE.match(ln)
            if m:
                lines.append((m.group(1), m.group(2)))
    lines.sort(key=lambda x: x[0])

    rounds: list[dict] = []
    pending: dict | None = None
    for ts, body in lines:
        if "requests in" in body:
            if pending:
                rounds.append(pending)
            mm = re.search(r'(\d+)\s+requests in', body)
            pending = {"report_ts": ts, "requests": int(mm.group(1)) if mm else None,
                       "non2xx": 0}
        elif "Non-2xx or 3xx responses:" in body and pending is not None:
            try:
                pending["non2xx"] = int(body.split(":")[1].strip())
            except ValueError:
                pass
        elif body.startswith("Running wrk2 on round") and pending is not None:
            rounds.append(pending); pending = None
    if pending:
        rounds.append(pending)
    return rounds


def wait_for_rounds(after_ts: str | None, need: int, label: str) -> dict:
    """Block until `need` complete rounds have REPORTED after `after_ts`."""
    say(f"  waiting for >= {need} complete wrk2 rounds ({label})"
        + (f" after {after_ts}" if after_ts else " since deploy"))
    t0 = time.monotonic()
    while time.monotonic() - t0 < ROUND_POLL_TIMEOUT:
        rounds = wrk2_rounds()
        sel = [r for r in rounds if after_ts is None or r["report_ts"] > after_ts]
        if len(sel) >= need:
            tot_req = sum(r["requests"] or 0 for r in sel)
            tot_bad = sum(r["non2xx"] for r in sel)
            res = {"rounds": len(sel), "first_round_utc": sel[0]["report_ts"],
                   "last_round_utc": sel[-1]["report_ts"], "total_requests": tot_req,
                   "total_non2xx": tot_bad,
                   "non2xx_rate": (tot_bad / tot_req) if tot_req else None,
                   "rounds_with_non2xx": sum(1 for r in sel if r["non2xx"] > 0),
                   "detail": sel, "timed_out": False,
                   "wait_seconds": round(time.monotonic() - t0, 1)}
            say(f"  {label}: {len(sel)} rounds, {tot_bad}/{tot_req} non-2xx "
                f"= {100*res['non2xx_rate']:.4f}%  ({res['wait_seconds']}s)")
            return res
        time.sleep(ROUND_POLL_INTERVAL)
    rounds = wrk2_rounds()
    sel = [r for r in rounds if after_ts is None or r["report_ts"] > after_ts]
    tot_req = sum(r["requests"] or 0 for r in sel); tot_bad = sum(r["non2xx"] for r in sel)
    say(f"  !! {label}: TIMED OUT with only {len(sel)} rounds")
    return {"rounds": len(sel), "first_round_utc": sel[0]["report_ts"] if sel else None,
            "last_round_utc": sel[-1]["report_ts"] if sel else None,
            "total_requests": tot_req, "total_non2xx": tot_bad,
            "non2xx_rate": (tot_bad / tot_req) if tot_req else None,
            "rounds_with_non2xx": sum(1 for r in sel if r["non2xx"] > 0),
            "detail": sel, "timed_out": True,
            "wait_seconds": round(time.monotonic() - t0, 1)}


# ------------------------------------------------------------------- evidence
def dump_state(out_dir: Path, label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "services": ["get", "services", "-n", NAMESPACE, "-o", "json"],
        "pods": ["get", "pods", "-n", NAMESPACE, "-o", "json"],
        "deployments": ["get", "deployments", "-n", NAMESPACE, "-o", "json"],
        "endpointslices": ["get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE, "-o", "json"],
        "user-service": ["get", "service", "user-service", "-n", NAMESPACE,
                         "--ignore-not-found", "-o", "json"],
    }
    man = {"label": label, "captured_utc": utc_now(), "files": {}}
    for name, args in targets.items():
        p = k(*args)
        path = out_dir / f"{name}.json"
        path.write_text(p.stdout, encoding="utf-8")
        man["files"][name] = {"exit_status": p.returncode, "bytes": len(p.stdout),
                              "sha256": hashlib.sha256(p.stdout.encode()).hexdigest()}
    (out_dir / "dump-manifest.json").write_text(
        json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    say(f"  dumped {label} -> {out_dir}")
    return man


# --------------------------------------------------------------------- oracles
def stop_noise_like_conductor(R, phase):
    """Replicates conductor.py:476-483: the conductor stops noise before EVERY
    evaluation, and NoiseManager.stop() (manager.py:84-95) also removes all active
    chaos experiments. Without this the run would not be faithful."""
    try:
        from sregym.generators.noise.manager import get_noise_manager
        get_noise_manager().stop()
        R.setdefault("noise_stopped_before", []).append({"phase": phase, "utc": utc_now()})
        say(f"  noise manager STOPPED before {phase} evaluation [conductor.py:476-483]")
    except Exception as e:
        say(f"  noise stop failed: {e}")


def evaluate_in_process(problem, phase: str) -> dict:
    say(f"  {phase} in-process evaluate() starting")
    started = utc_now(); t0 = time.monotonic()
    # ---- conductor.py:267-271 replicated verbatim ----
    try:
        r = problem.mitigation_oracle.evaluate()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        r = {"success": False, "error": f"{type(e).__name__}: {e}"}
    # --------------------------------------------------
    rec = {"started_utc": started, "finished_utc": utc_now(),
           "elapsed_seconds": round(time.monotonic() - t0, 3), "raw_verdict": r}
    say(f"  {phase} in-process raw verdict = {json.dumps(r, sort_keys=True)} "
        f"({rec['elapsed_seconds']}s)")
    return rec


def run_worker(out: Path, phase: str, baseline: dict) -> dict:
    wdir = out / "worker"; wdir.mkdir(parents=True, exist_ok=True)
    inp = wdir / f"{phase.lower()}-input.json"
    res = wdir / f"{phase.lower()}-result.json"
    if res.exists() or res.is_symlink():
        raise RuntimeError(f"worker output path already exists, refusing to write: {res}")
    doc = {"captured_replica_baseline": baseline,
           "evidence_paths": {"original_oracle_input": "worker/input.json",
                              "original_oracle_result": "worker/result.json",
                              "original_oracle_stderr": "worker/stderr.txt",
                              "original_oracle_stdout": "worker/stdout.txt"},
           "kubernetes_context": CONTEXT, "namespace": NAMESPACE}
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    inp.write_bytes(data)

    say(f"  {phase} SREMut worker starting")
    started = utc_now(); t0 = time.monotonic()
    p = subprocess.run(
        [str(STOCK_PY), "-I", "-B", str(WORKER), str(inp), str(res)],
        cwd=str(SREGYM_ROOT), shell=False, stdin=subprocess.DEVNULL,
        capture_output=True,
        env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
    )
    rec = {"started_utc": started, "finished_utc": utc_now(),
           "elapsed_seconds": round(time.monotonic() - t0, 3),
           "exit_code": p.returncode,
           "stdout": p.stdout.decode(errors="replace"),
           "stderr": p.stderr.decode(errors="replace"),
           "input_sha256": hashlib.sha256(data).hexdigest(),
           "input_bytes": len(data)}
    if res.is_file():
        rec["result"] = json.loads(res.read_text())
        rec["returned_boolean"] = rec["result"].get("returned_boolean")
        rec["outcome"] = rec["result"].get("outcome")
        rec["raw_result_sha256"] = rec["result"].get("raw_result_sha256")
    else:
        rec["result"] = None; rec["returned_boolean"] = None
        rec["outcome"] = "NO_RESULT_FILE"; rec["raw_result_sha256"] = None
    say(f"  {phase} worker exit={p.returncode} outcome={rec['outcome']} "
        f"returned_boolean={rec['returned_boolean']} ({rec['elapsed_seconds']}s)")
    return rec


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--single-instrument", action="store_true",
                    help="in-process oracle only; skip the SREMut worker (PROTOCOL_W3 — "
                         "the worker pins EXPECTED_NAMESPACE=social-network at "
                         "original_oracle_worker.py:23 and that pin is frozen)")
    ap.add_argument("--noise", action="store_true",
                    help="ConductorConfig(enable_noise=True) — PROTOCOL_W2 H3")
    ap.add_argument("--problem-id", default=PROBLEM_ID,
                    help="registry problem_id (PROTOCOL_W2 H4 uses missing_service_hotel_reservation)")
    ap.add_argument("--null-agent-delay", type=int, default=NULL_AGENT_EPISODE_SECONDS,
                    help="seconds between inject_fault() returning and the treatment "
                         "oracle (PROTOCOL_G1 R1 default 60; PROTOCOL_W1 uses 0)")
    args = ap.parse_args()
    delay = args.null_agent_delay
    noise = args.noise
    single = args.single_instrument
    problem_id = args.problem_id
    out = EXPERIMENTS / args.run_id
    out.mkdir(parents=True, exist_ok=True)

    R = {"schema_version": 1, "protocol": "sremut-g1-three-state-v1",
         "protocol_doc": "SREMut/experiments/PROTOCOL_G1.md",
         "run_id": args.run_id, "problem_id": problem_id, "enable_noise": None,
         "driver": "candidate-ii-minimal-driver",
         "agent_action_between_injection_and_evaluation": "NONE",
         "null_agent_episode_seconds": None,  # set below from --null-agent-delay
         "min_rounds_per_state": MIN_ROUNDS_PER_STATE,
         "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "started_at": utc_now(), "status": "RUNNING"}
    R["null_agent_episode_seconds"] = delay
    R["enable_noise"] = noise
    R["single_instrument"] = single
    if single:
        R["registered_deviation"] = ("PROTOCOL_W3: in-process oracle only; SREMut worker "
                                     "skipped because original_oracle_worker.py:23 pins "
                                     "EXPECTED_NAMESPACE='social-network' (frozen)")
    R["protocol_doc"] = ("SREMut/experiments/PROTOCOL_G1.md" if delay == NULL_AGENT_EPISODE_SECONDS
                         else "SREMut/experiments/PROTOCOL_W1.md (H2 submission-latency)")
    overall = time.monotonic()
    sampler = None

    try:
        # 1. sampler ---------------------------------------------------------
        say("STEP 1: starting state sampler")
        sampler = subprocess.Popen(
            [str(EXPERIMENTS / "sample_state.sh"), str(out / "samples.jsonl")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        R["sampler_pid"] = sampler.pid
        say(f"  sampler PID {sampler.pid}")
        time.sleep(3)

        # 2. deploy ----------------------------------------------------------
        say("STEP 2: Conductor / fix_kubernetes / undeploy_app / deploy_app  [MUTATING]")
        conductor = Conductor(config=ConductorConfig(deploy_loki=False, enable_noise=noise))
        conductor.problem_id = problem_id
        conductor.problem = conductor.problems.get_problem_instance(conductor.problem_id)
        conductor.app = conductor.problem.app
        problem = conductor.problem
        global NAMESPACE
        NAMESPACE = problem.namespace
        R["namespace"] = NAMESPACE
        R["mitigation_oracle_class"] = type(problem.mitigation_oracle).__name__
        t0 = time.monotonic(); R["deploy_started_utc"] = utc_now()
        conductor.fix_kubernetes(); conductor.undeploy_app(); conductor.deploy_app()
        if noise:
            # Replicates conductor.py:442-451, which three_state_run.py otherwise skips
            # because it does not call start_problem().
            from sregym.generators.noise.manager import get_noise_manager
            nm = get_noise_manager()
            nm.set_problem_context({"namespace": conductor.problem.app.namespace,
                                    "app_name": conductor.problem.app.name})
            nm.start()
            R["noise_started_utc"] = utc_now()
            say("  noise manager STARTED [replicates conductor.py:442-451]")
        R["deploy_finished_utc"] = utc_now()
        R["deployment_seconds"] = round(time.monotonic() - t0, 3)
        say(f"  deploy complete in {R['deployment_seconds']}s")

        # 3. wait_for_ready --------------------------------------------------
        say("STEP 3: wait_for_ready(max_wait=600)")
        t0 = time.monotonic()
        problem.kubectl.wait_for_ready(namespace=NAMESPACE, max_wait=600)
        R["stabilization_seconds"] = round(time.monotonic() - t0, 3)
        say(f"  stable in {R['stabilization_seconds']}s")

        # 4. healthy workload window  [R2] -----------------------------------
        say("STEP 4: healthy workload window  [R2]")
        R["workload_healthy"] = wait_for_rounds(None, MIN_ROUNDS_PER_STATE, "HEALTHY")

        # 5. capture_baseline  [conductor.py:227] ----------------------------
        say("STEP 5: capture_baseline()   [conductor.py:227]")
        problem.mitigation_oracle.capture_baseline()
        baseline = dict(problem.mitigation_oracle.replica_count)
        R["captured_replica_baseline"] = baseline
        R["captured_replica_baseline_count"] = len(baseline)
        R["captured_replica_baseline_sha256"] = hashlib.sha256(
            json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        (out / "replica-baseline.json").write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        say(f"  captured {len(baseline)} deployments")

        # 6. HEALTHY oracles -------------------------------------------------
        say("STEP 6: HEALTHY oracles")
        if noise: stop_noise_like_conductor(R, "HEALTHY")
        R["healthy_in_process"] = evaluate_in_process(problem, "HEALTHY")
        R["healthy_worker"] = ({"outcome": "SKIPPED_SINGLE_INSTRUMENT", "returned_boolean": None,
                                   "raw_result_sha256": None, "exit_code": None, "elapsed_seconds": None}
                                  if single else run_worker(out, "HEALTHY", baseline))

        # 7. GATE ------------------------------------------------------------
        ip_ok = R["healthy_in_process"]["raw_verdict"].get("success") is True
        wk_ok = True if single else (R["healthy_worker"]["returned_boolean"] is True)
        wl_ok = R["workload_healthy"]["total_non2xx"] == 0
        R["healthy_gate"] = {"in_process_true": ip_ok, "worker_true": wk_ok,
                             "zero_non2xx": wl_ok,
                             "healthy_non2xx": R["workload_healthy"]["total_non2xx"],
                             "passed": bool(ip_ok and wk_ok and wl_ok)}
        if not R["healthy_gate"]["passed"]:
            R["status"] = "STOPPED_AT_HEALTHY_GATE"
            say(f"!!! HEALTHY GATE FAILED: {R['healthy_gate']}. NOT INJECTING. STOP.")
            return 2
        say("  GATE PASSED (both oracles True, zero non-2xx). Proceeding.")

        # 8. healthy dump ----------------------------------------------------
        R["dump_healthy"] = dump_state(out / "healthy", "healthy")

        # 9. inject  [conductor.py:229] --------------------------------------
        say("STEP 9: inject_fault()   [conductor.py:229]  [MUTATING]")
        t0 = time.monotonic(); R["injection_started_utc"] = utc_now()
        problem.inject_fault()
        R["injection_finished_utc"] = utc_now()
        R["injection_seconds"] = round(time.monotonic() - t0, 3)
        R["fault_injected_flag"] = problem.fault_injected
        say(f"  injection complete in {R['injection_seconds']}s")

        # 10. R1 null-agent episode ------------------------------------------
        say(f"STEP 10: null-agent episode — sleeping {delay}s"
            + ("  [R1]" if delay == NULL_AGENT_EPISODE_SECONDS else "  [PROTOCOL_W1 H2: zero/short delay]"))
        R["null_agent_sleep_started_utc"] = utc_now()
        if delay > 0:
            time.sleep(delay)
        R["null_agent_sleep_finished_utc"] = utc_now()

        # 11. FAULTED oracles ------------------------------------------------
        say("STEP 11: FAULTED oracles")
        if noise: stop_noise_like_conductor(R, "FAULTED")
        R["faulted_in_process"] = evaluate_in_process(problem, "FAULTED")
        R["faulted_worker"] = ({"outcome": "SKIPPED_SINGLE_INSTRUMENT", "returned_boolean": None,
                                   "raw_result_sha256": None, "exit_code": None, "elapsed_seconds": None}
                                  if single else run_worker(out, "FAULTED", baseline))

        # 12. faulted workload window  [R2] ----------------------------------
        say("STEP 12: faulted workload window  [R2]")
        R["workload_faulted"] = wait_for_rounds(
            R["injection_finished_utc"].replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "FAULTED")

        # 13. faulted dump ---------------------------------------------------
        R["dump_faulted"] = dump_state(out / "faulted", "faulted")

        # 14. recover  [missing_service.py:52] -------------------------------
        say("STEP 14: recover_fault()   [missing_service.py:52]  [MUTATING]")
        t0 = time.monotonic(); R["recovery_started_utc"] = utc_now()
        problem.recover_fault()
        R["recovery_finished_utc"] = utc_now()
        R["recovery_seconds"] = round(time.monotonic() - t0, 3)
        R["fault_injected_flag_after_recovery"] = problem.fault_injected
        say(f"  recovery complete in {R['recovery_seconds']}s")

        # 15. restored workload window  [R2] ---------------------------------
        say("STEP 15: restored workload window  [R2]")
        R["workload_restored"] = wait_for_rounds(
            R["recovery_finished_utc"].replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "RESTORED")

        # 16. RESTORED oracles -----------------------------------------------
        say("STEP 16: RESTORED oracles")
        if noise: stop_noise_like_conductor(R, "RESTORED")
        R["restored_in_process"] = evaluate_in_process(problem, "RESTORED")
        R["restored_worker"] = ({"outcome": "SKIPPED_SINGLE_INSTRUMENT", "returned_boolean": None,
                                   "raw_result_sha256": None, "exit_code": None, "elapsed_seconds": None}
                                  if single else run_worker(out, "RESTORED", baseline))

        # 17. restored dump --------------------------------------------------
        R["dump_restored"] = dump_state(out / "restored", "restored")

        R["status"] = "COMPLETE"
        R["teardown_performed"] = False

    except Exception as error:
        R["status"] = "FAILED"
        R["error"] = {"type": type(error).__name__, "message": str(error),
                      "traceback": traceback.format_exc()}
        say(f"!!! FAILED: {type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        # 18. stop sampler ---------------------------------------------------
        if sampler is not None:
            say("STEP 18: stopping sampler")
            try:
                os.killpg(os.getpgid(sampler.pid), signal.SIGTERM)
                time.sleep(2)
                if sampler.poll() is None:
                    os.killpg(os.getpgid(sampler.pid), signal.SIGKILL)
            except Exception:
                pass
        R["finished_at"] = utc_now()
        R["total_seconds"] = round(time.monotonic() - overall, 3)
        (out / "three-state.json").write_text(
            json.dumps(R, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        say(f"three-state.json -> {out / 'three-state.json'}  status={R['status']}")
    return 0 if R["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
