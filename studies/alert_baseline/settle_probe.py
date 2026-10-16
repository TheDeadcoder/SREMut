"""One settle episode: once the fault is detected, the problem's mitigation oracle is judged again and again for WINDOW
seconds after each of three states, in order: the fault with no repair, every pod of the namespace restarted with
the fault in place, and SREGym's recovery. Every evaluation keeps when it started and how long it took, so
settle_ledger.py can read both the verdict right after each state and the verdict of an oracle that waits up to a
limit for the alerts to clear.

Runs under the SREGym virtual environment with the SREGym checkout as working directory.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep"), str(HERE.parent / "verifier_mutation")]

from operators import apply  # noqa: E402
from probe import DETECT_TIMEOUT, POLL, alert_oracles, alert_state, capture, clean_up, poll_until  # noqa: E402
from triage_probe import evaluate, profile  # noqa: E402

WINDOW = 600


def window(oracle, namespace, start, baselines, excluded, seconds=WINDOW):
    begin, polls = time.monotonic(), []
    while time.monotonic() - begin < seconds:
        at = time.monotonic() - begin
        verdict = evaluate(oracle)
        polls.append({"at": round(at), "took": round(time.monotonic() - begin - at), "verdict": verdict,
                      "alerts": alert_state(namespace, start, baselines, excluded)})
        time.sleep(POLL)
    return {"profile": profile([poll["verdict"] for poll in polls]), "polls": polls}


def run(problem_id):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    out = {"problem_id": problem_id}
    conductor, injected, recovered = None, False, False
    try:
        conductor = Conductor(config=ConductorConfig(deploy_loki=False))
        problem = conductor.problems.get_problem_instance(problem_id)
        conductor.problem_id, conductor.problem, conductor.app = problem_id, problem, problem.app
        oracle, namespace = problem.mitigation_oracle, problem.namespace
        alerts = alert_oracles(oracle)
        excluded = set().union(*(alert.exclude_alerts for alert in alerts))
        out["alert_oracles"] = len(alerts)

        conductor.dependency_check(["kubectl", "helm", "docker"])
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        start = time.monotonic()
        baseline, out["baseline"] = capture(oracle, alerts, start)
        baselines = {"immediate": baseline}

        injected = True
        problem.inject_fault()
        out["detect"] = poll_until(oracle, namespace, DETECT_TIMEOUT, False, start, baselines, excluded)
        out["detected"] = "-" in out["detect"]["profile"]
        if out["detected"]:
            out["none"] = window(oracle, namespace, start, baselines, excluded)
            out["restart"] = {"applied": apply("RESTART", namespace),
                              **window(oracle, namespace, start, baselines, excluded)}
        problem.recover_fault()
        recovered = True
        out["repair"] = window(oracle, namespace, start, baselines, excluded)
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out.update(clean_up(conductor, injected, recovered))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problem", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    from logger import init_logger

    init_logger()
    result = run(args.problem)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()
