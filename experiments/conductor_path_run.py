"""G3 — production-path verdict via the REAL Conductor, driven in-process.

Obtains self.results["Mitigation"] as produced by Conductor._evaluate_mitigation
(conductor.py:262-279), after a null submission, using the conductor's own stage
machinery: start_problem() -> _build_stage_sequence / _advance_to_next_stage /
_inject_fault -> submit().

NOT used: main.py, any Docker image, any LLM credential, register_agent,
start_k8s_proxy. Nothing is written into SREGym/.

TWO DISCLOSED INTERVENTIONS, both strictly outside the verdict path:
  1. _inject_fault is wrapped to timestamp entry/exit; it calls the original
     unchanged (observation only).
  2. _finish_problem is neutralised. _advance_to_next_stage(2) (conductor.py:505
     -> :317) would call _finish_problem -> _cleanup_sync, which at
     conductor.py:341-350 runs problem.recover_fault(), problem.app.cleanup()
     and reconcile_to_baseline -- a full teardown. It executes strictly AFTER
     _evaluate_mitigation has assigned results["Mitigation"] at :272 and TTM at
     :273, so it cannot influence the verdict. It is suppressed because the
     protocol forbids teardown and the faulted state is needed for the
     post-verdict workload window.

Protocol: SREMut/experiments/PROTOCOL_G1.md R1 (>= 60 s between inject_fault()
returning and the mitigation evaluation) is enforced explicitly; the actual
interval is recorded either way.

Run under SREGym/.venv/bin/python with cwd=SREGym.
"""

from __future__ import annotations

import argparse, asyncio, hashlib, json, os, re, signal, subprocess, time, traceback
from datetime import UTC, datetime
from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig

PINNED_KUBECTL = "/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
CONTEXT = "kind-kind"
NAMESPACE = "social-network"
PROBLEM_ID = "missing_service_social_network"
EXPERIMENTS = Path("/home/sakibbuet2k19/sremut/SREMut/experiments")
NULL_AGENT_EPISODE_SECONDS = 60          # R1
MIN_ROUNDS = 10                          # R2-style post-verdict window


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def say(m: str) -> None:
    print(f"[{utc_now()}] {m}", flush=True)


def k(*a):
    return subprocess.run([PINNED_KUBECTL, "--context", CONTEXT, *a], capture_output=True, text=True)


TS_RE = re.compile(r'^(\S+Z)\s(.*)$')


def wrk2_rounds():
    # NOTE: the \n inside the jsonpath must reach kubectl as a two-character escape.
    # A single-quoted Python literal turns it into a real newline and kubectl then fails
    # with 'unterminated quoted string' (exit 1, empty stdout). That defect voided the
    # faulted workload window of g3-run-01; see g3-run-01/conductor-path.md.
    pods = k("get", "pods", "-n", NAMESPACE, "-l", "job-name=wrk2-job",
             "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}").stdout.split()
    lines = []
    for p in pods:
        for ln in k("logs", p, "-n", NAMESPACE, "--timestamps").stdout.splitlines():
            m = TS_RE.match(ln)
            if m:
                lines.append((m.group(1), m.group(2)))
    lines.sort(key=lambda x: x[0])
    rounds, pending = [], None
    for ts, body in lines:
        if "requests in" in body:
            if pending:
                rounds.append(pending)
            mm = re.search(r'(\d+)\s+requests in', body)
            pending = {"report_ts": ts, "requests": int(mm.group(1)) if mm else None, "non2xx": 0}
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


def wait_rounds(after_ts, need, label, timeout=600):
    say(f"  waiting for >= {need} complete wrk2 rounds ({label}) after {after_ts}")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        sel = [r for r in wrk2_rounds() if r["report_ts"] > after_ts]
        if len(sel) >= need:
            req = sum(r["requests"] or 0 for r in sel); bad = sum(r["non2xx"] for r in sel)
            out = {"rounds": len(sel), "first_round_utc": sel[0]["report_ts"],
                   "last_round_utc": sel[-1]["report_ts"], "total_requests": req,
                   "total_non2xx": bad, "non2xx_rate": (bad / req) if req else None,
                   "rounds_with_non2xx": sum(1 for r in sel if r["non2xx"] > 0),
                   "timed_out": False, "detail": sel}
            say(f"  {label}: {len(sel)} rounds, {bad}/{req} non-2xx = {100*out['non2xx_rate']:.4f}%")
            return out
        time.sleep(10)
    sel = [r for r in wrk2_rounds() if r["report_ts"] > after_ts]
    req = sum(r["requests"] or 0 for r in sel); bad = sum(r["non2xx"] for r in sel)
    say(f"  !! {label}: TIMED OUT with {len(sel)} rounds")
    return {"rounds": len(sel), "total_requests": req, "total_non2xx": bad,
            "non2xx_rate": (bad / req) if req else None, "timed_out": True, "detail": sel}


def dump_state(out_dir: Path, label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tg = {"services": ["get", "services", "-n", NAMESPACE, "-o", "json"],
          "pods": ["get", "pods", "-n", NAMESPACE, "-o", "json"],
          "deployments": ["get", "deployments", "-n", NAMESPACE, "-o", "json"],
          "endpointslices": ["get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE, "-o", "json"],
          "user-service": ["get", "service", "user-service", "-n", NAMESPACE, "--ignore-not-found", "-o", "json"]}
    man = {"label": label, "captured_utc": utc_now(), "files": {}}
    for n, a in tg.items():
        p = k(*a); (out_dir / f"{n}.json").write_text(p.stdout, encoding="utf-8")
        man["files"][n] = {"exit_status": p.returncode, "bytes": len(p.stdout),
                           "sha256": hashlib.sha256(p.stdout.encode()).hexdigest()}
    (out_dir / "dump-manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    say(f"  dumped {label} -> {out_dir}")
    return man


async def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    out = EXPERIMENTS / args.run_id; out.mkdir(parents=True, exist_ok=True)

    R = {"schema_version": 1, "protocol": "sremut-g3-conductor-path-v1",
         "run_id": args.run_id, "problem_id": PROBLEM_ID,
         "driver": "candidate-i-real-conductor-in-process",
         "main_py_used": False, "docker_image_built": False, "llm_credentials_set": False,
         "disclosed_interventions": [
             "_inject_fault wrapped for timestamps only; original called unchanged",
             "_finish_problem neutralised (runs after conductor.py:272 assigns the verdict; "
             "suppressed because _cleanup_sync at conductor.py:341-350 would recover_fault + "
             "undeploy + reconcile, which the protocol forbids and which would destroy the "
             "faulted state needed for the post-verdict workload window)"],
         "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "started_at": utc_now(), "status": "RUNNING"}
    sampler = None; overall = time.monotonic()

    try:
        say("starting state sampler")
        sampler = subprocess.Popen([str(EXPERIMENTS / "sample_state.sh"), str(out / "samples.jsonl")],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        R["sampler_pid"] = sampler.pid; time.sleep(3)

        say("constructing the REAL Conductor")
        conductor = Conductor(config=ConductorConfig(deploy_loki=False, enable_noise=False))
        conductor.problem_id = PROBLEM_ID

        # ---- intervention 1: timestamp _inject_fault (observation only) ----
        orig_inject = conductor._inject_fault
        def timed_inject():
            R["injection_started_utc"] = utc_now()
            t = time.monotonic()
            say(">>> conductor._inject_fault() entered [conductor.py:219]")
            orig_inject()
            R["injection_finished_utc"] = utc_now()
            R["injection_seconds"] = round(time.monotonic() - t, 3)
            R["_inject_mono"] = time.monotonic()
            say(f"<<< conductor._inject_fault() returned after {R['injection_seconds']}s")
        conductor._inject_fault = timed_inject

        # ---- intervention 2: neutralise post-verdict teardown ----
        def blocked_finish():
            R["finish_problem_reached_utc"] = utc_now()
            R["finish_problem_suppressed"] = True
            say("*** _finish_problem() reached and SUPPRESSED (post-verdict teardown) ***")
        conductor._finish_problem = blocked_finish

        say("await conductor.start_problem()  [conductor.py:388]  [CLUSTER-MUTATING]")
        t0 = time.monotonic()
        res = await conductor.start_problem()
        R["start_problem_result"] = str(res)
        R["start_problem_seconds"] = round(time.monotonic() - t0, 3)
        R["tasklist"] = list(conductor.tasklist or [])
        R["stage_sequence"] = [s["name"] for s in conductor.stage_sequence]
        R["submission_stage_after_start"] = conductor.submission_stage
        R["fault_injected_flag"] = conductor.fault_injected
        say(f"  start_problem -> {res}; tasklist={R['tasklist']}; stages={R['stage_sequence']}; "
            f"stage now '{conductor.submission_stage}'; fault_injected={conductor.fault_injected}")

        async def submit_and_wait(label):
            say(f"await conductor.submit(None)  [{label}]  [conductor.py:516]")
            ack = await conductor.submit(None)
            say(f"  submit ack: {ack}")
            fut = conductor._submit_future
            if fut is not None:
                await asyncio.wrap_future(fut)
            say(f"  {label} evaluation complete; stage now '{conductor.submission_stage}'")
            return ack

        # ---- diagnosis stage ----
        if conductor.submission_stage == "diagnosis":
            R["diagnosis_submit_ack"] = await submit_and_wait("diagnosis")
        else:
            R["diagnosis_submit_ack"] = f"SKIPPED (stage was '{conductor.submission_stage}')"

        # ---- R1: >= 60 s between injection returning and the mitigation evaluation ----
        elapsed = time.monotonic() - R.get("_inject_mono", time.monotonic())
        need = max(0.0, NULL_AGENT_EPISODE_SECONDS - elapsed)
        R["r1_elapsed_before_wait_seconds"] = round(elapsed, 3)
        R["r1_extra_wait_seconds"] = round(need, 3)
        say(f"R1: {elapsed:.3f}s already elapsed since injection returned; sleeping a further {need:.3f}s")
        if need > 0:
            time.sleep(need)

        # ---- mitigation stage ----
        R["mitigation_stage_before_submit"] = conductor.submission_stage
        if conductor.submission_stage != "mitigation":
            R["status"] = "MITIGATION_STAGE_NOT_REACHED"
            say(f"!!! expected stage 'mitigation', found '{conductor.submission_stage}'. STOP.")
            return 2
        R["mitigation_submit_ack"] = await submit_and_wait("mitigation")

        # ---- capture the conductor's own results dict, verbatim ----
        R["conductor_results"] = json.loads(json.dumps(dict(conductor.results), default=str))
        R["results_Mitigation"] = R["conductor_results"].get("Mitigation")
        R["results_Diagnosis"] = R["conductor_results"].get("Diagnosis")
        R["results_TTM"] = R["conductor_results"].get("TTM")
        R["results_TTL"] = R["conductor_results"].get("TTL")
        say("=== conductor.results ===")
        say(json.dumps(R["conductor_results"], indent=2, sort_keys=True))

        R["dump_faulted"] = dump_state(out / "faulted", "faulted-post-verdict")

        # ---- post-verdict faulted workload window ----
        anchor = R.get("injection_finished_utc", "").replace("+00:00", "Z")
        R["workload_faulted"] = wait_rounds(anchor, MIN_ROUNDS, "FAULTED")

        # ---- restore through SREGym's own path ----
        say("problem.recover_fault()  [missing_service.py:52]  [CLUSTER-MUTATING]")
        t0 = time.monotonic(); R["recovery_started_utc"] = utc_now()
        conductor.problem.recover_fault()
        R["recovery_finished_utc"] = utc_now()
        R["recovery_seconds"] = round(time.monotonic() - t0, 3)
        say(f"  recovery complete in {R['recovery_seconds']}s")
        R["dump_restored"] = dump_state(out / "restored", "restored")
        R["status"] = "COMPLETE"

    except Exception as e:
        R["status"] = "FAILED"
        R["error"] = {"type": type(e).__name__, "message": str(e), "traceback": traceback.format_exc()}
        say(f"!!! FAILED: {type(e).__name__}: {e}"); traceback.print_exc()
    finally:
        if sampler is not None:
            say("stopping sampler")
            try:
                os.killpg(os.getpgid(sampler.pid), signal.SIGTERM); time.sleep(2)
                if sampler.poll() is None:
                    os.killpg(os.getpgid(sampler.pid), signal.SIGKILL)
            except Exception:
                pass
        R.pop("_inject_mono", None)
        R["finished_at"] = utc_now(); R["total_seconds"] = round(time.monotonic() - overall, 3)
        (out / "conductor-path.json").write_text(json.dumps(R, indent=2, sort_keys=True, default=str) + "\n")
        say(f"conductor-path.json -> {out/'conductor-path.json'}  status={R['status']}")
    return 0 if R["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
