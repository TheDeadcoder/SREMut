# G0.2-recon — Null-agent experiment plan

Status: PLAN ONLY. Nothing in this document has been executed. No cluster contact,
no deploy, no injection, no oracle run, no tests, no uv/pip, no git write command
occurred while producing it.

- Prepared: 2026-08-26
- SREGym commit: `ba07faf1a322f9b6d4a279643bb796aa2f36f64b`
- Prerequisite analysis: `SREMut/analysis/G01_mitigation_verdict_path.md`
- Method: static reading + grep evidence. Absence claims are backed by a grep that
  returns nothing and are labelled "VERIFIED ABSENT". Inference is labelled as
  inference. Undeterminable items are stated as undeterminable.

Paths are relative to `/home/sakibbuet2k19/sremut/SREGym/` unless prefixed `SREMut/`,
`harness/`, `baselines/`, or `manifests/`.

---

## Hypothesis under test (H1)

SREGym's `missing_service` fault injector deletes `Service/user-service`, then runs
`kubectl delete pods --all -n social-network` and `wait_for_stable`
(`inject_virtual.py:306-307`). The stock `MitigationOracle` observes only Deployment
replica counts and Pod/container readiness (established in G0.1). THEREFORE the
freshly-injected, un-mitigated fault state MAY satisfy every predicate the oracle
depends on, and `Mitigation.success` MAY be true with no agent action whatsoever.

H1 is a hypothesis. This document plans the experiment that would test it. Nothing
here asserts H1 is true.

---

## Pre-registered interpretation rule

Registered 2026-08-26, BEFORE the experiment was executed and before any result
existed. This rule governs how a `False` verdict is to be classified, and is fixed in
advance precisely so that classification cannot be chosen after seeing the outcome.

> A False verdict falsifies H1 only if every pod in the namespace was in
> phase Running with all containers ready at the moment of evaluation, and
> the False is attributable to a Deployment predicate (mitigation.py
> conditions 1-3). A False attributable to any pod not in phase Running --
> including the wrk2 workload pod -- is classified harness_timing_failure,
> is excluded from the result set, is preserved, and the repetition is
> re-run. This rule is registered before execution.

Cross-reference: "mitigation.py conditions 1-3" are the Deployment predicates
enumerated in `SREMut/analysis/G01_mitigation_verdict_path.md` §A.2 —
(1) a baseline Deployment is absent, `mitigation.py:70-73`;
(2) `spec.replicas == 0`, `mitigation.py:76-79`;
(3) `ready_replicas < desired`, `mitigation.py:81-84`.
Conditions 4-6 are the pod-sweep predicates at `mitigation.py:88-112`.

A precedent for this classification already exists in the project record: the
three-run baseline report classified a transient workload-pod race as a harness timing
failure and excluded it —
`baselines/missing_service_social_network/baseline-reproducibility.json`,
`protocol_notes[1]`: "The transient Run 1 workload-pod race is classified as a harness
timing failure and excluded from results." This rule generalises that precedent and
fixes it in advance.

---

## A. Current cluster state — from local files only

NO CLUSTER CONTACT was made. No kubectl, no docker, no Kubernetes API call.

| Evidence | Value | Source |
|---|---|---|
| Host boot time | **2026-08-25 17:38:09** | `uptime -s`, `who -b` |
| Uptime at analysis | 16 h 54 m (at 2026-08-26 10:33) | `uptime` |
| Kubeconfig | present; `current-context: kind-kind` (line 12) | `~/.kube/config`, mtime 2026-08-15 14:09:19 |
| Last baseline run | run-03, `VALID_HEALTHY_BASELINE`, captured 2026-08-16T14:47:36Z | `baselines/.../run-03/baseline-summary.json` |
| Teardown after run-01 | `CLEANUP_PASS` 2026-08-15T14:47:15Z | `teardown-after-run-01/cleanup-summary.json` |
| Teardown after run-02 | `CLEANUP_PASS` 2026-08-16T14:38:20Z | `teardown-after-run-02/cleanup-summary.json` |
| Teardown after run-03 | **VERIFIED ABSENT** (`ls -d teardown-after-run-03` -> No such file or directory) | — |
| Cluster baseline state | **exists**, 8166 bytes, mtime 2026-08-15 14:20 | `~/cache_dir/cluster_baseline_state.json` |

Note: there are THREE baseline runs (run-01, run-02, run-03), not two. An earlier
survey truncated the listing.

### Three findings

1. **The last on-disk record leaves `social-network` DEPLOYED.** Runs 01 and 02 each
   have a matching `CLEANUP_PASS` teardown directory; run-03 has none. As of
   2026-08-16T14:47Z the app was up and was never recorded as torn down.

2. **`/tmp` was wiped by the reboot.** `/tmp/..` and every system subdirectory
   (`.ICE-unix`, `.X11-unix`, `.XIM-unix`, `.font-unix`, `snap-private-tmp`, and the
   three `systemd-private-*` dirs) carry mtime 2026-08-25 17:38 — the boot timestamp.
   - `ls /tmp/*user-service*` -> No such file or directory. **VERIFIED ABSENT.**
   - `ls /tmp/*_modified.yaml` -> No such file or directory. **VERIFIED ABSENT.**

   CONSEQUENCE FOR THE PROJECT RULES: the hard rule "Never delete anything under /tmp;
   it holds preserved forensics" now protects nothing — no file under /tmp predates
   2026-08-25 17:38. The Aug 15-16 forensics are gone. Flagged, not fixed.

3. **Whether the kind cluster or the app is up RIGHT NOW is NOT DETERMINABLE from
   disk.** Ten days and a host reboot separate the last artifact from now. Kind nodes
   are Docker containers whose survival across a reboot depends on the Docker daemon's
   restart policy and service enablement; there is no on-disk record of either.
   `~/.kube/config` proves a cluster was CONFIGURED, not that one is RUNNING — its
   mtime (2026-08-15) predates even run-02. Step 1 of §F resolves this with a
   read-only probe.

---

## B. How SREGym deploys social_network

Entrypoint: `Conductor.deploy_app()`, `conductor.py:782`, body through line 874.

| Line | Installs |
|---|---|
| 820-825 | OpenEBS storageclass patch, `wait_for_ready("openebs")`, device storageclass |
| 827-828 | Prometheus |
| 830-831 | Jaeger |
| 833-834 | OTel Collector |
| 836-840 | Loki — SKIPPED when `ConductorConfig(deploy_loki=False)` |
| 842-843 | MCP server |
| 863 | `problem.app.deploy()` — Helm install of socialNetwork |
| 866-868 | Jaeger `ExternalName` service in the app namespace |
| 870-872 | `problem.app.start_workload()`, gated on `run_default_workload` (`problems/base.py:7`, default `True`) |

Leaves behind: namespaces `social-network`, `observe`, `openebs`; Helm release
`social-network`; the `wrk2-job` workload Job (`capture_healthy_baseline.sh:133`);
27 Deployments, 28 Pods, 30 Services (`run-03/baseline-summary.json` counts).

Measured timings across the three baseline runs
(`baselines/missing_service_social_network/baseline-reproducibility.json`):

| Phase | Mean | Range | CV |
|---|---|---|---|
| `deploy_app` | **157.655 s** | 155.347 – 160.103 | 1.51 % |
| `wait_for_ready` stabilization | 8.696 s | 8.663 – 8.729 | — |
| Stock oracle `evaluate()` | **20.833 s** | 20.75 – 20.916 | — |
| Total | 188.831 s | 187.495 – 190.166 | — |

The 20.8 s oracle time on a HEALTHY cluster indicates `_wait_for_rollouts` returns
early rather than consuming its 60 s budget when deployments are settled.

### Do the harness scripts drive SREGym's deploy, or reimplement it?

**They drive SREGym's own deploy.** `harness/baseline_runner.py` imports
`Conductor, ConductorConfig` (line 10) and calls:

- `conductor.fix_kubernetes()` — line 84
- `conductor.undeploy_app()` — line 85
- `conductor.deploy_app()` — line 86

No reimplementation: `grep -c 'helm install' harness/baseline_runner.py` -> 0.

`harness/capture_healthy_baseline.sh` deploys nothing. It is pure post-hoc evidence
capture: `kubectl get -o json` (lines 37-112), `helm get manifest/values/list`
(114-127), `kubectl logs job/wrk2-job` (131-135), `jq -e` invariant assertions
(148-192), `SHA256SUMS` seal (264-273).

**KEY PLANNING FACT.** `baseline_runner.py` already performs
`deploy -> wait_for_ready -> capture_baseline() -> evaluate()` at lines 86, 95, 108,
112 respectively, and has done so successfully three times with a 1.51 % deployment
CV. The null-agent experiment is that exact sequence with ONE statement inserted
between lines 108 and 112.

---

## C. Driving the fault injection

### `run-oracle.py` (108 lines) end to end

Docstring, lines 3-5: "This script assumes the application is already deployed and
the fault is already injected. It only runs the mitigation oracle to verify the
system state."

Flow: `ProblemRegistry()` (36) -> `get_problem_ids()` membership check (39-43) ->
`get_problem_instance(problem_id)` (47) -> `hasattr` guard (50-52) ->
`problem.mitigation_oracle.evaluate()` (57) -> summary print (93-101) ->
`sys.exit(0 if result.get("success", False) else 1)` (104).

Arguments: exactly one, `--problem` (line 76). No `--deploy`, no `--inject`, no
`--baseline`.

**Could it do deploy -> capture_baseline -> inject_fault -> evaluate? NO, on three
independent counts:**

1. Never deploys — `grep -n 'deploy' run-oracle.py` returns nothing. VERIFIED ABSENT.
2. Never injects — `grep -n 'inject_fault' run-oracle.py` returns nothing. VERIFIED ABSENT.
3. Never captures a baseline — `grep -n 'capture_baseline' run-oracle.py` returns
   nothing. VERIFIED ABSENT. So `replica_count` stays `{}` and the oracle's Deployment
   checks (G0.1 conditions 1-3) are skipped entirely.

`run-oracle.py` is UNUSABLE as the experiment driver: it would evaluate a strictly
weaker oracle than any real conductor run, making the result trivially contestable.
It remains useful as a secondary artifact demonstrating that a shipped SREGym entry
point evaluates with an empty baseline.

### Candidate (i) — real Conductor with a null agent

**A null agent is reachable, and SREGym ships one.**
`clients/autosubmit/autosubmit_agent.py`, 32 lines: loops up to 10000 times, each
iteration `curl -X POST {server}/submit -d '{"solution":"yes"}'` then `sleep(60)`.

It performs ZERO cluster work:
`grep -cE 'kubectl|kubernetes|helm' clients/autosubmit/autosubmit_agent.py` -> 0.
**VERIFIED ABSENT.**

Registered as a first-class agent at `agents.yaml:8-9`:

    - name: autosubmit
      kickoff_command: python -m clients.autosubmit.autosubmit_agent

Intended invocation shown at `tests/e2e-testing-scripts/automating_tests.py:199`:
`main.py --agent autosubmit`.

**What `submit()` requires** (`conductor.py:516-568`): `self.waiting_for_agent` must
be `True`, else `RuntimeError("Conductor is not currently waiting for an agent
submission.")` at line 548. If evaluation is already running it returns a benign
"Submission already accepted" (line 543). The solution string is passed through but
for mitigation is NEVER USED — `conductor.py:265`. The 60 s poll means a premature
POST simply retries.

**The diagnosis-stage objection dissolves.** `tasklist.yml` DOES NOT EXIST — only
`tasklist.yml.example`; `ls sregym/conductor/tasklist.yml` -> No such file or
directory. Per `conductor.py:132-137` the conductor therefore defaults to
`["diagnosis", "mitigation"]`. (This also closes an open question left by G0.1.)
But diagnosis cannot block mitigation:

- `DiagnosisJudge.__init__` (`llm_as_a_judge/judge.py:123+`) sets `self._backend = None`
  lazily and reads only a local `rca_checklists.yaml`. It accepts
  `api_key: str | None = None` and never raises for a missing key —
  `grep -n 'raise |getenv|environ\[' judge.py` returns only lines 115, 491, 494, 502,
  all inside response PARSING. So `MissingService.__init__` will not fail without
  credentials.
- If diagnosis evaluation does fail, `conductor.py:249-251` catches it, records
  `{"success": False, "error": ...}`, and `conductor.py:503-505` advances to the next
  stage regardless.

So (i) runs without LLM credentials AND without creating `tasklist.yml`. For the
record: `sregym/conductor/tasklist.yml` is gitignored (`git check-ignore -v` ->
`.gitignore:223`), so creating it would not dirty SREGym's git working tree — but it
would still be a write into the read-only reference tree, and is NOT proposed here.
It must not be created without explicit approval.

**Residual unknown for (i):** whether `LAUNCHER` runs `autosubmit`'s kickoff command
directly or inside a Docker container image that must first be built. `main.py:214-220`
resolves the agent from `agents.yaml` and calls `conductor.register_agent`;
`main.py:465-467` references `LAUNCHER._container_runner.config.image` defaulting to
`sregym-agent-base:latest`. I did not trace the launcher far enough to determine which
path `autosubmit` takes. **NOT DETERMINED STATICALLY — must be verified at runtime.**

### Candidate (ii) — minimal driver reproducing conductor.py 227 -> 229 -> 268

Every deviation from the real conductor path, however small:

| # | Deviation | Consequence |
|---|---|---|
| 1 | No `get_problem_stages()` / `_build_stage_sequence()` | `stage_sequence` never built; no stage-registration gate (`conductor.py:204`). Cosmetic — the gate only checks the oracle is non-None. |
| 2 | No `submit()` / `waiting_for_agent` handshake | Agent-submission plumbing skipped. Solution is unused (`conductor.py:265`), so no verdict effect — but a reviewer must be shown that line. |
| 3 | No `self.results["Mitigation"]`, no `TTM` | Raw dict must be recorded by the driver instead of `conductor.py:272-273`. No CSV `Mitigation.success` cell produced. |
| 4 | No diagnosis stage | `results["Diagnosis"]` absent. Independent of mitigation (G0.1 §C), so no verdict effect. |
| 5 | `_evaluate_mitigation` try/except absent unless replicated | An oracle exception would propagate instead of becoming `{"success": False, "error": ...}` (`conductor.py:269-271`). **MUST BE REPLICATED** — the one deviation that could change a recorded outcome. |
| 6 | No `_finish_problem()` / `_cleanup_sync()` | Teardown must be driven explicitly (§F step 12). |
| 7 | No noise manager | `baseline_runner.py` already sets `enable_noise=False`; all three baselines ran this way. |
| 8 | `deploy_loki=False` | Same as all three baselines. Loki is not in the app namespace and is not observed by the oracle. |
| 9 | No agent container, no run publishing | No `main.py` run directory or results CSV. |

Deviations 1, 2, 4, 7, 8, 9 provably cannot reach the oracle's inputs. Deviation 5 is
a genuine fidelity requirement. Deviations 3 and 6 are driver responsibilities.

### RECOMMENDATION

**Run (ii) as the primary instrument, then (i) as production-path corroboration.
If only one run is possible, run (ii).**

Reasoning, on evidence quality rather than convenience:

The paper's claim has two halves that must hold SIMULTANEOUSLY — the oracle says
repaired, AND the system is broken. Candidate (i) drives everything autonomously
through `main.py`; there is no seam at which to capture Service / EndpointSlice / DNS
state in the seconds bracketing `evaluate()`. It can prove half the claim very well —
a genuine `Mitigation.success` cell produced by the real harness — and cannot prove
the other half at the verdict moment at all.

Candidate (ii) gives exact control of the interleaving, which is what the simultaneity
claim requires, and its contestability is BOUNDED AND ENUMERABLE: the table above is
the complete deviation list, and only row 5 touches outcome fidelity.

Ordering matters too: (ii) is cheap to re-run and its failure modes are legible, so
establishing the result there first means (i) is run once, to confirm, rather than
debugged blind. Running both closes the loop — (ii) proves both halves
simultaneously; (i) proves the production harness records the same Boolean.

---

## D. What could falsify H1

Falsification route: deleting `user-service` makes some pod non-`Running` or some
container not-ready, so the oracle returns `False` on its own merits.

### Probes — VERIFIED ABSENT

`_baseDeployment.tpl` is the template every DeathStarBench microservice uses. All 90
lines read: NO `readinessProbe`, NO `livenessProbe`, NO `startupProbe`, NO
`initContainers`. Chart-wide greps:

- `readinessProbe` -> ONE hit, `values.yaml:64`, inside the `redis-cluster` sub-chart
  block, `enabled: false`
- `livenessProbe` -> ONE hit, `values.yaml:66`, also `enabled: false`
- `startupProbe` -> **no output. VERIFIED ABSENT.**
- `initContainers` -> ONE hit, `templates/_baseNginxDeployment.tpl:66`

**Cross-checked against CAPTURED LIVE CLUSTER STATE**, not just the chart. Parsing
`run-03/deployments.json` (27 deployments) and `run-03/pods.json` (28 pods) for
`readinessProbe` / `livenessProbe` / `startupProbe` on every container:
**probes found: NONE** in both files. Init containers found: exactly two,
`alpine-container` in `media-frontend` and in `nginx-thrift`.

Those init containers run
`git clone https://github.com/delimitrou/DeathStarBench.git` plus file copies
(`charts/nginx-thrift/values.yaml`) — reaching the public internet, not any cluster
Service. `grep -rn 'user-service' charts/nginx-thrift/` -> **no output. VERIFIED
ABSENT.**

WITH NO PROBES ANYWHERE, a container is "ready" as soon as its process starts. There
is no application-level health gate that a missing Service could trip.

### Startup connection logic

Two services consume `user-service`:
`src/ComposePostService/ComposePostService.cpp:64-68,100` and
`src/SocialGraphService/SocialGraphService.cpp:64-68`. Both construct a `ClientPool`.

`ClientPool`'s constructor (`src/ClientPool.h:52-70`) pre-populates with
`for (int i = 0; i < min_pool_size; ++i)`. At the `user-service` call site
(`ComposePostService.cpp:99-101`) the fourth argument — `min_size` — is **`0`**, as it
is at every pool call site in lines 90-110. The loop body never executes; NO client
object is created at startup.

Even if one were, `ThriftClient`'s constructor (`src/ThriftClient.h:59-70`) only
constructs a `TSocket` and wraps it in transport/protocol layers. `Connect()` is a
SEPARATE method, declared at `src/ThriftClient.h:46`. Constructing a `TSocket`
performs no DNS lookup and opens no connection.

**INFERENCE (labelled as inference):** these two facts together make a startup crash
from a missing `user-service` very unlikely, so the most plausible failure mode is
request-time errors inside a still-`Running`, still-ready pod. Nothing was compiled or
executed; a runtime abort in a code path not read cannot be ruled out.

### What CANNOT be determined statically

- Whether any container aborts at startup for reasons unrelated to probes.
- Whether the `kubectl delete pods --all` restart produces transient
  `CrashLoopBackOff` or `ImagePullBackOff`.
- Whether the wrk2 workload Job pod enters a failing phase visible to the oracle's
  namespace-wide sweep. **This is a live falsification risk:** the oracle iterates ALL
  pods in the namespace (`mitigation.py:95`), and run-03 recorded 28 pods against 27
  deployments — the extra is the workload pod.

### `wait_for_stable` timeout and failure behaviour

`kubectl.py:280-299`: `wait_for_stable(self, namespace, sleep=2, max_wait=300)` —
**300 s ceiling, 2 s poll**. Returns when
`all(self.is_ready(pod) for pod in pod_list.items)`. On expiry it **RAISES** at line
299.

The exception text is
`f"[red]Timeout: Namespace '{namespace}' was not deleted within {max_wait} seconds."`
— an upstream copy-paste artifact; the function has nothing to do with deletion.
Recorded here so the message is not misread in a log.

`is_ready` (`kubectl.py:255-278`) is notably permissive — it returns `True` for:

- phase `Succeeded` or `Failed` (260-261)
- phase `Running` with all containers ready (263-265)
- **`CrashLoopBackOff`** (267-271)
- phase `Pending` with `PodScheduled=False` (273-276)

So `wait_for_stable` can converge on a cluster containing crash-looping pods, which
the oracle would then REJECT at `mitigation.py:102-104`. The two functions disagree;
that gap should be watched during the run.

### If `wait_for_stable` does not converge

The raise propagates: `inject_virtual.py:307` -> `inject_missing_service` ->
`_invoke_method` (`fault/base.py:58-65`) -> `_inject` (`fault/base.py:39-46`) ->
`MissingService.inject_fault`. The `@mark_fault_injected` wrapper
(`sregym/utils/decorators.py:6-8`) **RE-RAISES for `inject_fault`** rather than
swallowing. Injection failure is therefore fatal and loud: the driver aborts before
any oracle call, `self.fault_injected` is never set (line 13 unreachable), and no
verdict is produced.

That is the correct behaviour for this experiment — a failed injection can never be
mistaken for a result.

---

## E. Evidence bundle at the verdict moment

The frozen policy imposes a hard ordering constraint:
`challenge_creation_minimum_state: ORIGINAL_ORACLE_EVALUATED`
(`SREMut/policies/missing_service_social_network/evidence-capture-v1.1.yaml:2552`).
**The challenge pod may not be created until AFTER the oracle has been evaluated.**
DNS and HTTP probes therefore come after the oracle; API-level broken-state evidence
brackets it on both sides.

| # | Evidence | Command | Timing | Type |
|---|---|---|---|---|
| E1 | `Service/user-service` absent | `kubectl get service user-service -n social-network -o json` (expect NotFound, exit 1) | T-, T+ | READ-ONLY |
| E2 | Full Service list (29, not 30) | `kubectl get services -n social-network -o json` | T-, T+ | READ-ONLY |
| E3 | No EndpointSlice for user-service | `kubectl get endpointslices.discovery.k8s.io -n social-network -l kubernetes.io/service-name=user-service -o json` (expect `.items == []`) | T-, T+ | READ-ONLY |
| E4 | Legacy Endpoints absent | `kubectl get endpoints user-service -n social-network -o json` (expect NotFound) | T-, T+ | READ-ONLY |
| E5 | Oracle's own view: Deployments | `kubectl get deployments -n social-network -o json` | T-, T+ | READ-ONLY |
| E6 | Oracle's own view: Pods | `kubectl get pods -n social-network -o json` | T-, T+ | READ-ONLY |
| E7 | **Stock oracle Boolean + raw dict** | §F step 9 (SREMut worker) | **T** | READ-ONLY (oracle issues GET/LIST only) |
| E8 | DNS does not resolve | `nslookup user-service.social-network.svc.cluster.local` inside challenge pod | T+ | **CLUSTER-MUTATING** |
| E9 | TCP/HTTP through the FQDN fails | `wget -q -T 10 -O - http://user-service.social-network.svc.cluster.local:9090/` inside challenge pod | T+ | **CLUSTER-MUTATING** |

Port 9090 is correct: `capture_healthy_baseline.sh:187` asserts
`any(.spec.ports[]; .port == 9090)` on the healthy `user-service`, and
`openshift/nginx-thrift-config/lua-scripts/wrk2-api/user/register.lua:45` connects to
`user-service.social-network.svc.cluster.local` on 9090.

### Ordering — AMENDED: continuous sampling, not a T-/T+ pair

AMENDMENT A2 (2026-08-26, before execution). Two point-in-time captures cannot exclude
a transition inside the oracle's ~21 s observation window: a Service could in principle
reappear and vanish between them. The T-/T+ pair is therefore REPLACED as the primary
simultaneity evidence by a continuous sampler, with the full JSON dumps RETAINED at T-
and T+ as the high-fidelity anchors at each end.

**E0 — continuous sampler (new, primary).** `SREMut/experiments/sample_state.sh`
appends one compact JSON line every 2 seconds for the whole run, covering:

| Field | Type | Meaning |
|---|---|---|
| `ts_utc` | string | ISO 8601, millisecond precision |
| `user_service_present` | bool | Service/user-service exists |
| `user_service_endpointslice_count` | int | EndpointSlices labelled `kubernetes.io/service-name=user-service` |
| `service_count` | int | Services in `social-network` |
| `deployments` | `[{name, ready, desired}]` | all Deployments in the namespace |
| `pods` | `[{name, phase, all_containers_ready}]` | all Pods in the namespace |

Constraints: uses the PINNED kubectl at
`/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl`, not the host default
(the host default is v1.36.3 per `manifests/host-environment.txt`, and the execution
profile pins v1.32.0). Uses `custom-columns` / `jsonpath` projections rather than
`-o json`, so each line stays small. It mutates nothing and survives a `NotFound`
without exiting.

**Revised ordering:**

1. E0 sampler starts BEFORE the driver and runs continuously to the end (2 s cadence)
2. E1-E6 full JSON dumps immediately before the oracle (T- anchor)
3. E7, the oracle (~21 s)
4. E1-E6 full JSON dumps immediately after the oracle (T+ anchor)
5. E0 sampler stopped after the driver exits

The sampler proves the broken state held CONTINUOUSLY across the oracle's entire
observation window at 2-second resolution, not merely at its two endpoints. That is
the evidence that makes the simultaneity claim airtight, and it is exactly what
candidate (i) cannot produce.

A second purpose, equally important: the sampler is the instrument that lets the
pre-registered interpretation rule be applied at all. Classifying a False as
`harness_timing_failure` requires knowing every pod's phase AT the moment of
evaluation — which a T-/T+ pair can only bracket, and a 2 s sampler can actually
show.

### Anything requiring a pod to run a command

E8 and E9 only. Cluster DNS and ClusterIP routing exist only inside the pod network
and cannot be observed from the host. Two options:

- `kubectl exec` into an existing app pod — creates no Kubernetes object, but executes
  inside a production container and its tooling is not guaranteed. **REJECTED:** it
  perturbs the system under observation.
- **A dedicated challenge pod**, which the frozen policy already specifies
  (`evidence-capture-v1.1.yaml:1373-1400`): pinned busybox by digest
  `sha256:b7f3d86d6e84fc17718c48bcde1450807faa2d56704205c697b4bd5df7b9e29f`,
  `automount_service_account_token: false`, `restart_policy: Never`,
  `active_deadline_seconds: 600`, `termination_grace_period_seconds: 5`,
  `run_as_non_root: true`, uid/gid 65532, `read_only_root_filesystem: true`,
  `allow_privilege_escalation: false`, all capabilities dropped,
  `seccomp_profile: RuntimeDefault`. Exec via
  `CoreV1Api.connect_get_namespaced_pod_exec` with a pod-UID check immediately before
  and after every exec (`evidence-capture-v1.1.yaml:1324-1331`).

Creating that pod IS CLUSTER-MUTATING and is labelled as such. It is read-only with
respect to the system under test — it adds one pod and touches nothing else — but it
is not a read-only operation. It may be deferred entirely on a first pass (E1-E7 only).

---

## F. Exact execution plan

Every step is labelled READ-ONLY or CLUSTER-MUTATING. NOTHING BELOW HAS BEEN RUN.

Environment for all SREGym-driving steps: `cd /home/sakibbuet2k19/sremut/SREGym` with
`SREGym/.venv/bin/python`. Evidence root below is written as `$EV`; choose a path
under `SREMut/` (NOT under `/tmp`).

### Step 1 — READ-ONLY — cluster liveness probe (~5 s)

    kubectl --context kind-kind cluster-info
    kubectl --context kind-kind get nodes -o wide

Expected if up: 4 nodes `Ready` (matches `manifests/cluster-smoke-*.txt`
`node_count=4`, `ready_node_count=4`). If this fails, the cluster did not survive the
2026-08-25 reboot — go to Step 2b.

### Step 2a — READ-ONLY — application liveness probe (~5 s)

    kubectl --context kind-kind get ns
    kubectl --context kind-kind get deployments -n social-network
    helm list -n social-network -o json

Expected if the app survived: namespace `social-network` present, 27 deployments,
Helm release `social-network`. Per §A the last on-disk record leaves it deployed, but
this is NOT determinable from disk and must be observed here.

If the app IS present and healthy, Steps 3-4 may be skipped — but see the CAUTION in
Step 4.

### Step 2b — CLUSTER-MUTATING — recreate the kind cluster if needed (~100 s)

Only if Step 1 failed. Follow the recorded procedure in
`manifests/kind-recreate-20260815T140837Z.log` (kind create + Calico CNI). The host
environment is unchanged per `manifests/host-environment.txt` (kind v0.32.0,
kubectl v1.36.3, Kubernetes server v1.32.0, node image pinned by digest).

### Step 3 — CLUSTER-MUTATING — clean deploy (~158 s)

Drive SREGym's own deploy exactly as `harness/baseline_runner.py:84-86` does:

    conductor = Conductor(config=ConductorConfig(deploy_loki=False, enable_noise=False))
    conductor.problem_id = "missing_service_social_network"
    conductor.problem = conductor.problems.get_problem_instance(conductor.problem_id)
    conductor.app = conductor.problem.app
    conductor.fix_kubernetes()
    conductor.undeploy_app()
    conductor.deploy_app()

Expected: ~157.7 s (three-run mean; CV 1.51 %).

CAUTION: `undeploy_app()` then `deploy_app()` guarantees a known-clean starting state.
Skipping Steps 3-4 to reuse a surviving deployment saves ~170 s but forfeits that
guarantee and leaves the starting state unverified against the frozen baseline.
Recommended: always run Steps 3-4.

### Step 4 — CLUSTER-MUTATING — wait for readiness (~9 s)

    conductor.problem.kubectl.wait_for_ready(namespace="social-network", max_wait=600)

Expected: ~8.7 s (three-run mean). Raises on timeout.

### Step 5 — READ-ONLY — pre-fault evidence + faithful replica baseline (~10 s)

**5a. Capture the healthy Service spec — INSURANCE AGAINST §G's /tmp risk:**

    kubectl get service user-service -n social-network -o yaml > "$EV/pre/user-service.yaml"
    kubectl get service user-service -n social-network -o json > "$EV/pre/user-service.json"

**5b. Capture the faithful replica baseline at exactly the conductor's position**
(`conductor.py:227`, immediately before injection):

    problem = conductor.problem
    problem.mitigation_oracle.capture_baseline()
    # dump problem.mitigation_oracle.replica_count to "$EV/pre/replica-baseline.json"

Expected: 27 entries, every value `1` (cross-check against
`baselines/missing_service_social_network/run-01/deployments.json`). This map becomes
the SREMut worker's `captured_replica_baseline` in Step 9.

**5c. Full healthy snapshot** — run `harness/capture_healthy_baseline.sh` semantics or
the E1-E6 command batch against the healthy state.

### Step 6 — CLUSTER-MUTATING — inject the fault (~30-310 s)

    problem.inject_fault()          # conductor.py:229

Internally (`inject_virtual.py:295-307`): capture Service YAML (298) -> `kubectl
delete service user-service` (299-301) -> write `/tmp/user-service_modified.yaml`
(303) -> `kubectl delete pods --all -n social-network` (306) -> `wait_for_stable`
(307, <=300 s) -> plus `time.sleep(6)` at `fault/base.py:46`.

Expected: prints `== Fault Injection ==` then
`Service: user-service | Namespace: social-network`. RAISES on `wait_for_stable`
timeout (see §D) — that is a clean abort, not a result.

### Step 7 — READ-ONLY — T- evidence batch (~2 s)

Run E1-E6 into `"$EV/t-minus/"`. Expected: E1 and E4 return NotFound (exit 1); E3
returns `.items == []`; E2 lists 29 services; E5/E6 show all Deployments ready and all
Pods Running/ready.

Capture wall-clock UTC immediately before and after this batch.

### Step 8 — READ-ONLY — in-process oracle invocation (~21 s)

Replicate `conductor.py:268` INSIDE the try/except of lines 269-271 (deviation 5):

    try:
        r = problem.mitigation_oracle.evaluate()
    except Exception as e:
        r = {"success": False, "error": f"{type(e).__name__}: {e}"}
    # record r verbatim to "$EV/verdict/in-process-oracle.json"

Expected: ~21 s. `r` is `{"success": <bool>}` (G0.1 §A.3).

### Step 9 — READ-ONLY — independent stock-oracle Boolean via the SREMut worker (~25 s)

Corrected path from G0.1 §F. Write the canonical request first — sorted keys, compact
separators, no trailing newline — substituting the REAL 27-entry map from Step 5b for
the placeholder:

    {"captured_replica_baseline":{"...27 entries...":1},"evidence_paths":{"original_oracle_input":"e/input.json","original_oracle_result":"e/result.json","original_oracle_stderr":"e/stderr.txt","original_oracle_stdout":"e/stdout.txt"},"kubernetes_context":"kind-kind","namespace":"social-network"}

Then (output path must NOT already exist):

    cd /home/sakibbuet2k19/sremut/SREGym && \
    env -i LC_ALL=C.UTF-8 PATH=/usr/bin:/bin \
            PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      /home/sakibbuet2k19/sremut/SREGym/.venv/bin/python -I -B \
      /home/sakibbuet2k19/sremut/SREMut/src/sremut/original_oracle_worker.py \
      "$EV/verdict/input.json" "$EV/verdict/worker-result.json"

Exit codes (`original_oracle_worker.py:294-311`): 0 success, 64 wrong argv count,
65 `WorkerFailure` (code on stderr), 70 unexpected exception. Result carries
`returned_boolean` and `outcome` in {`RETURNED_TRUE`, `RETURNED_FALSE`,
`ORACLE_EXCEPTION`, `ORIGINAL_ORACLE_RETURN_SHAPE_INVALID`}.

CAVEAT (from G0.1 §F): invoking the worker directly bypasses the adapter, so there is
no git tag/submodule provenance verification (`original_oracle_adapter.py:220-275`),
no evidence-policy candidates, no journal entry, and no timeout enforcement. Adequate
as a second independent measurement; NOT a substitute for a full adapter run.

### Step 10 — READ-ONLY — T+ evidence batch (~2 s)

Repeat E1-E6 into `"$EV/t-plus/"`. Expected: byte-identical broken-state findings to
Step 7. Capture wall-clock UTC.

Together, Steps 7 and 10 establish that Service/EndpointSlice absence held across the
entire oracle observation window.

### Step 11 — OPTIONAL, CLUSTER-MUTATING — challenge pod for E8/E9 (~30 s)

Permitted only now (`evidence-capture-v1.1.yaml:2552`). Create the pod exactly per
`challenge_pod_template` (§E), exec `nslookup` and `wget`, capture stdout/stderr,
verify the pod UID immediately before and after each exec, then delete the pod.

Expected: `nslookup` fails to resolve; `wget` fails to connect.

May be omitted on a first pass.

### Step 12 — CLUSTER-MUTATING — teardown / restore (~60-200 s)

    problem.recover_fault()         # missing_service.py:52

then

    SREGym/.venv/bin/python harness/controlled_cleanup.py \
      --source-run-id <run-id> --output <path>/cleanup-result.json

`controlled_cleanup.py` calls `problem.app.cleanup()` (line 60), loads
`~/cache_dir/cluster_baseline_state.json` (67-72), and reconciles to baseline (78).
Expected: `"status": "CLEANUP_PASS"`.

If Step 11 ran, confirm the challenge pod is gone
(`active_deadline_seconds: 600` self-terminates it as a backstop).

### Step 13 — REQUIRED — CLUSTER-MUTATING — production-path run, candidate (i)

AMENDMENT A3 (2026-08-26, before execution). Driver (i) is PROMOTED from optional
corroboration to a REQUIRED second run. The experiment is not complete without it.

Rationale for the promotion: driver (ii) proves both halves of the claim
simultaneously but is a reimplementation, and its nine enumerated deviations (§C) are
an argument a reviewer can attack. Driver (i) is the unmodified production path and
yields the benchmark's own recorded artifact — the results CSV cell
`Mitigation.success` — produced by an agent SREGym itself ships. Neither run alone
closes the claim: (ii) without (i) invites "you did not run the real harness"; (i)
without (ii) cannot show the system was broken at the verdict moment. Both are
required.

Separate run, own deploy:

    SREGym/.venv/bin/python main.py --agent autosubmit

The E0 sampler MUST also run for the duration of this run, so the production-path
verdict carries the same continuous broken-state evidence.

Verify at runtime whether `LAUNCHER` runs the kickoff command directly or via a
container image (unresolved, §C). Expect diagnosis to be attempted first (recorded as
`Diagnosis.success = False` without LLM credentials — see §C for why this cannot block
mitigation) and mitigation ~60 s later.

Target artifact: the results CSV cell `Mitigation.success`, plus the sampler timeline
covering its evaluation window.

Sequencing note: Step 13 requires its own deploy and injection, so it runs AFTER the
Step 12 teardown of the driver-(ii) run — not concurrently. The two runs are
independent repetitions, not a shared cluster state.

---

## G. Risks and recovery

### The `/tmp/user-service_modified.yaml` question — RESOLVED

`recover_fault()` (`missing_service.py:52-59`) calls `recover_missing_service`
(`inject_virtual.py:309-320`), which at lines 314-315 runs
`kubectl apply -f /tmp/{service}_modified.yaml`. That file is written during
INJECTION by `_write_yaml_to_file` (`inject_virtual.py:3123-3130`) from the Service
spec captured at line 298 before deletion.

**It does NOT survive between runs, and it does not exist right now.**
`ls /tmp/*_modified.yaml` -> No such file or directory. **VERIFIED ABSENT** — wiped by
the 2026-08-25 17:38 reboot.

Consequences:

- Recovery works within a single inject -> recover cycle in one boot session.
- A reboot between injection and recovery makes `recover_fault()` silently useless:
  `kubectl apply -f` on a missing file fails, and `@mark_fault_injected` SWALLOWS the
  error for `recover_fault` (`decorators.py:9-11` warns and sets `result = None`
  rather than raising).

**MITIGATION (Step 5a):** capture `kubectl get service user-service -n social-network
-o yaml` into the SREMut evidence tree BEFORE injection. Never rely solely on `/tmp`.

### Risk table

| Risk | Blocks later work how | Recovery |
|---|---|---|
| `wait_for_stable` raises after 300 s; namespace left faulted mid-injection | Service deleted, pods restarting, no verdict | `recover_fault()` if the `/tmp` file was written this session; else `kubectl apply -f "$EV/pre/user-service.yaml"`; else `controlled_cleanup.py` + full redeploy |
| Cluster did not survive the reboot | Nothing can run | Recreate per `manifests/kind-recreate-20260815T140837Z.log`; host env unchanged per `manifests/host-environment.txt` |
| Host reboots mid-experiment | `/tmp` recovery spec lost | Step 5a copy in `$EV` is the authoritative restore source |
| `ProblemRegistry.get_problem_instance` contacts the cluster (`registry.py:365`, `is_emulated_cluster()`) | Not a risk per se — but means EVERY driver requires a live cluster; no dry run is possible | Plan accordingly; Step 1 must pass first |
| Teardown blocked / cluster left dirty | Later runs start from an unknown state | `~/cache_dir/cluster_baseline_state.json` EXISTS (8166 bytes, 2026-08-15 14:20, survived the reboot), so `controlled_cleanup.py` can reconcile |
| Challenge pod orphaned | Stray pod in the namespace | `active_deadline_seconds: 600` self-terminates; explicit delete in Step 12 |
| Workload `wrk2-job` pod in a failing phase | Oracle's namespace-wide sweep (`mitigation.py:95`) may return False for a reason unrelated to H1 | Capture `kubectl get pods -n social-network -o json` at T- and T+ and inspect the extra pod (28 pods vs 27 deployments) before interpreting any False |
| Candidate (i) launcher needs a Docker image build | Step 13 stalls | Unresolved statically; verify at runtime. Step 13 is optional and does not gate the primary result |

### Standing constraints observed by this plan

- Nothing is written into `SREGym/`. `sregym/conductor/tasklist.yml` is NOT created.
- Nothing under `/tmp` is deleted. (Note that per §A there is nothing there to
  preserve any more.)
- The frozen artifacts under `contracts/`, `profiles/`, `policies/`, `schemas/`,
  `mutants/` are read, never modified. The challenge-pod ordering constraint at
  `evidence-capture-v1.1.yaml:2552` is respected rather than worked around.
- No step executes until approved.

---

## Note added 2026-08-28 (R2 Part D1) — git does not corroborate the ordering claim

The statement above that this protocol was registered before execution is **left
unmodified and is not withdrawn**. What follows is the independent-corroboration status,
recorded so that no reader takes the statement as attested by version control.

**Git does not corroborate it.** The commit that introduced this file **postdates every
execution it governs.**

- Introducing commit: `9314bda3`, commit date **2026-08-26T20:11:56Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `g02-run-01` | 2026-08-26T11:00:19.367292Z |

- Relation to its own evidence: **its evidence was committed EARLIER, in 388872a3 at 2026-08-26T14:27:43Z**.

The session transcript records this protocol being written before the run started, and
the file's content is consistent with that. But the transcript is not a timestamping
authority, and SREMut has no git remote, so every timestamp here originates on a single
machine with a user-writable clock and is attested by no external service.

Full forensic record, including the four annotated tags whose pre-registration *is*
supported by git: `analysis/PREREGISTRATION_TIMELINE.md`.

