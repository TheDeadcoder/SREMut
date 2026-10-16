# Rating sheet

For each item, decide from the evidence whether a correct verifier for this task should accept the
state after the action (the fault is repaired and the application serves its users) or reject it.
Write accept, reject or unsure for each item in the answers file, with an optional note.

## R01

Task: SREGym `operator_non_existent_storage`

Fault: The TiDBCluster storage spec references a StorageClass that does not exist, so PVC provisioning fails and stateful pods cannot mount volumes or start.

Not fully ready after the fault: statefulsets/basic-pd

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: basic-discovery.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: statefulsets/basic-tidb, statefulsets/basic-tikv
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: basic-tidb, basic-tidb-peer
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: persistentvolumeclaims/pd-basic-pd-0, statefulsets/basic-pd

## R02

Task: AIOpsLab `assign_to_non_existent_node_social_net-mitigation-1`

Fault: The `user-service` deployment of the Social Network application is assigned to a node that does not exist or is not ready.

Not fully ready after the fault: deployments/user-service

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/user-service
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: user-service
- User requests: 10 per round over 1 round, 1 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: deployments/user-service

## R03

Task: SREGym `hpa_missing_effective_cpu_request_hotel_reservation`

Fault: The frontend autoscaling control loop is broken because `Deployment/frontend` produces pods without an effective CPU request, while `HorizontalPodAutoscaler/frontend-capacity` is configured to scale that deployment using CPU utilization. CPU utilization is calculated relative to CPU requests, so the HPA cannot compute the metric and reports `<unknown>/60%`, `ScalingActive=False`, and `FailedGetResourceMetric` with a message like `missing request for cpu`. The frontend pods may still be Running/Ready; the fault is the missing effective CPU request on the frontend workload causing the HPA control loop to fail. A complete diagnosis should identify both the missing effective CPU request and the resulting HPA CPU-utilization metric failure. Merely reporting that pods are healthy, or merely noting a missing CPU request without tying it to HPA/autoscaling behavior, is incomplete. A valid mitigation restores a computable CPU metric, typically by restoring CPU requests. Manually scaling the deployment without fixing the HPA is not sufficient.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: consul.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/consul
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: consul
- User requests: 2972 per round over 2 rounds, 0 with failures (before the fault: 2952 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: deployments/frontend

## R04

Task: AIOpsLab `k8s_target_port-misconfig-mitigation-3`

Fault: The Kubernetes Service `post-storage-service` of the Social Network application is given a wrong target port.

Action:

An agent worked on the task. Its commands:
1. `kubectl get pods -n test-social-network -o wide; echo ---; kubectl get svc -n test-social-network; echo ---; kubectl get events -n test-social-network --sort-by=.lastTimestamp | tail -40`
2. `cd /tmp; kubectl get events -n test-social-network --field-selector type=Warning 2>&1 | tail -20; echo ---; kubectl get deploy -n test-social-network -o wide; echo ---; for d in $(kubectl get deploy -n test-social-network -o name); do echo "== $d"; kubectl logs -n test-social-network $d --all-contai ...`
3. `cd /tmp; kubectl get endpoints post-storage-service -n test-social-network; echo ---; kubectl logs -n test-social-network deploy/post-storage-service --tail=60; echo ---; kubectl get deploy post-storage-service -n test-social-network -o yaml | sed -n '1,200p' | grep -vE "^\s+(uid|resourceVersion|cre ...`
4. `cd /tmp; kubectl get svc post-storage-service -n test-social-network -o yaml | grep -vE "^\s+(uid|resourceVersion|creationTimestamp):"; echo ---; kubectl get svc -n test-social-network -o custom-columns=NAME:.metadata.name,PORT:.spec.ports[0].port,TARGET:.spec.ports[0].targetPort; echo ---; kubectl  ...`
5. `cd /tmp; kubectl patch svc post-storage-service -n test-social-network --type=json -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":9090}]'; sleep 5; kubectl get endpoints post-storage-service -n test-social-network; echo ---; kubectl logs -n test-social-network deploy/compose-post-ser ...`
6. `cd /tmp; sleep 20; echo "refused since patch:"; kubectl logs -n test-social-network deploy/compose-post-service --since=20s 2>&1 | grep -c "Connection refused"; echo "--- post-storage recent:"; kubectl logs -n test-social-network deploy/post-storage-service --since=30s 2>&1 | tail -3; echo "--- tcp  ...`
Its closing message: The anomaly is mitigated: the `post-storage-service` Service was pointing at the wrong container port, and I corrected it. **What I found** - All 27 pods were Running with 0 restarts, so the problem was not pod health. - `compose-post-service` and `nginx-thrift` logged repeated `Connection refused` errors connecting to `post-storage-service:9090`. This broke the compose-post path. - The `post-storage-service` Service had `targetPort: 9999`, but the container listens on 9090 (`containerPort: 9090`, and the service config uses 9090). The Service resolved to an endpoint at `10.244.1.228:9999`, wh ...

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R05

Task: SREGym `sidecar_port_conflict_social_network`

Fault: A sidecar container binds the same listening port as the primary application container, causing startup-time port binding conflicts that prevent pods from becoming Ready.

Not fully ready after the fault: deployments/user-service

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 1024 per round over 5 rounds, 0 with failures (before the fault: 1024 per round over 5 rounds, 1 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R06

Task: SREGym `wrong_service_selector_astronomy_shop`

Fault: The service `frontend` has a misconfigured selector that adds an incorrect label, so it no longer matches the intended backing pods. The service has zero or insufficient endpoints despite healthy-looking deployments, causing routing failures at the service layer. Users observe connection resets/timeouts and partial outages when requests are sent through this service.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: frontend
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: services/frontend

## R07

Task: ITBench `58`

Fault: ITBench fault `scaled-to-zero-kubernetes-workload`: the Deployment `accounting` of the OpenTelemetry demo (namespace `otel-demo`) is scaled to zero.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n otel-demo get pods -o wide 2>&1 | head -100; echo ---; kubectl -n otel-demo get deploy,svc,cm 2>&1 | head -80`
2. `cd /tmp; kubectl -n otel-demo get deploy accounting -o yaml 2>&1 | head -150; echo ---EVENTS; kubectl -n otel-demo get events --sort-by=.lastTimestamp 2>&1 | tail -40; echo ---HPA; kubectl -n otel-demo get hpa,scaledobject,pdb 2>&1; echo ---ANN; kubectl -n otel-demo get deploy -o custom-columns=NAME ...`
3. `cd /tmp; kubectl -n otel-demo get configmap otel-collector -o yaml 2>&1; echo ---; kubectl -n otel-demo get configmap agent-fixtures -o yaml 2>&1; echo ---; kubectl -n otel-demo get configmap flagd-config -o yaml 2>&1 | head -80`
4. `cd /tmp; kubectl -n otel-demo get deploy accounting -o jsonpath='{range .metadata.managedFields[*]}{.manager}{" "}{.operation}{" "}{.time}{" "}{.fieldsV1}{"\n"}{end}' 2>&1; echo ---OPAMP; kubectl -n otel-demo logs deploy/opamp-server --tail=60 2>&1; echo ---COLLECTOR; kubectl -n otel-demo logs otel- ...`
5. `cd /tmp; kubectl -n otel-demo logs otel-collector-847fb8fc48-9cqkf --tail=2000 2>&1 | grep -E '\b(error|warn|Error|WARN)\b|panic' | cut -c1-500 | sort | uniq -c | sort -rn | head -30; echo ---; kubectl -n otel-demo get configmap otel-collector -o jsonpath='{.data.relay}' > /tmp/otel-relay.yaml 2>&1; ...`
6. `cd /tmp; sed -n '100,296p' /tmp/otel-relay.yaml; echo ---KAFKA; kubectl -n otel-demo logs deploy/kafka --tail=30 2>&1 | cut -c1-300`
7. `cd /tmp; kubectl -n otel-demo get endpoints chatbot mcp accounting 2>&1; echo ---SVC; kubectl -n otel-demo get svc chatbot mcp -o yaml 2>&1 | grep -A6 -E 'selector|ports' ; echo ---LOGS; kubectl -n otel-demo logs deploy/chatbot --tail=25 2>&1 | cut -c1-300; echo ---MCP; kubectl -n otel-demo logs dep ...`
8. `cd /tmp; kubectl -n otel-demo get configmap flagd-config -o jsonpath='{.data}' 2>&1 | head -c 3000; echo; echo ---ENV-CHATBOT; kubectl -n otel-demo get deploy chatbot mcp -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{range .spec.template.spec.containers[*]}{range .env[*]}{.name}={.value}{"\n" ...`
9. `cd /tmp; kubectl -n otel-demo logs deploy/load-generator --tail=20 2>&1 | cut -c1-250; echo ---ENVLG; kubectl -n otel-demo get deploy load-generator -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' 2>&1; echo ---FP; kubectl -n otel-demo get configmap -o name  ...`
10. `cd /tmp; kubectl -n otel-demo exec deploy/otel-collector -- wget -qO- http://localhost:8888/metrics 2>/dev/null | grep -E 'otelcol_exporter_send_failed|otelcol_receiver_refused' | head -15; echo ---SCALE; kubectl -n otel-demo get deploy -o custom-columns=NAME:.metadata.name,REPL:.spec.replicas,READY ...`
11. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python -c "import urllib.request,sys;print(urllib.request.urlopen('http://localhost:8089/stats/requests',timeout=5).read()[:1500].decode())" 2>&1 | head -60; echo ---ENVOY; kubectl -n otel-demo exec deploy/frontend-proxy -- wget -qO- http:/ ...`
12. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python -c " import urllib.request,json d=json.loads(urllib.request.urlopen('http://localhost:8089/stats/requests',timeout=5).read()) for s in d['stats']: print(s['method'],s['name'],'reqs=',s['num_requests'],'fails=',s['num_failures']) prin ...`
13. `cd /tmp; kubectl -n otel-demo get configmap flagd-config -o jsonpath='{.data.demo\.flagd\.json}' > /tmp/flagd-demo.json 2>&1; kubectl -n otel-demo exec deploy/flagd -c flagd -- sh -c 'cat /etc/flagd/demo.flagd.json' > /tmp/flagd-live.json 2>&1; python3 -I -c " import json for path in ['/tmp/flagd-de ...`
14. `cd /tmp; grep -c . /tmp/flagd-demo.json 2>/dev/null; grep -E '"defaultVariant": *"(?!off)' -P /tmp/flagd-demo.json 2>/dev/null; echo ---; grep -B6 -E '"defaultVariant": "[^o]' /tmp/flagd-demo.json | grep -E '^\s{4}"|defaultVariant|state' ; echo ---LIVE; wc -c /tmp/flagd-live.json; head -c 300 /tmp/f ...`
15. `cd /tmp; grep -o '"defaultVariant[^,]*' /tmp/flagd-demo.json | sort | uniq -c; grep -o 'state[^,]*' /tmp/flagd-demo.json | sort | uniq -c; kubectl -n otel-demo logs deploy/flagd -c flagd --tail=20 2>&1 | cut -c1-250`
16. `cd /tmp; kubectl -n otel-demo logs deploy/frontend --tail=40 2>&1 | cut -c1-300; echo ---CHECKOUT; kubectl -n otel-demo logs deploy/checkout --tail=30 2>&1 | cut -c1-300; echo ---FP-ADMIN; kubectl -n otel-demo exec deploy/frontend-proxy -- wget -qO- http://localhost:9901/clusters 2>&1 | grep -E 'hea ...`
17. `cd /tmp; kubectl -n otel-demo exec deploy/otel-collector -- wget -qO- http://localhost:8889/metrics > /tmp/coll-metrics.txt 2>&1; wc -l /tmp/coll-metrics.txt; grep -oE 'service_name="[^"]*"' /tmp/coll-metrics.txt | sort | uniq -c; echo ---; grep -E '^(http|rpc|otel|process)[a-z_]*\{' /tmp/coll-metri ...`
18. `cd /tmp; cat /tmp/coll-metrics.txt | head -5; kubectl -n otel-demo exec deploy/otel-collector -- sh -c 'wget -qO- http://localhost:8889/metrics' 2>&1 | head -c 500; echo; echo ---PROM; kubectl -n otel-demo get deploy prometheus 2>&1 | head -3; kubectl -n otel-demo get pods -l app.kubernetes.io/name= ...`
19. `cd /tmp; POD=otel-collector-847fb8fc48-9cqkf; (kubectl -n otel-demo port-forward pod/$POD 18889:8889 >/tmp/pf.log 2>&1 &) ; sleep 3; curl -s http://localhost:18889/metrics > /tmp/coll-metrics.txt; pkill -f 'port-forward pod/otel-collector' ; wc -l /tmp/coll-metrics.txt; grep -oE 'service_name="[^"]* ...`
20. `cd /tmp; for p in $(ls /proc | grep -E '^[0-9]+$'); do if tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q 'port-forward pod/otel-collector'; then kill $p 2>/dev/null && echo killed $p; fi; done; kubectl -n otel-demo exec deploy/load-generator -- python -c " import urllib.request t=urllib.reques ...`
21. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python -c " import urllib.request t=urllib.request.urlopen('http://otel-collector:8889/metrics',timeout=10).read().decode() print(t)" > /tmp/coll-metrics.txt 2>/tmp/coll-err.txt; wc -l /tmp/coll-metrics.txt; head -3 /tmp/coll-err.txt; grep  ...`
22. `cd /tmp; grep -vE '^#' /tmp/coll-metrics.txt | sed -E 's/[{ ].*//' | sort | uniq -c | sort -rn | head -60; echo ---KAFKA; grep -E 'kafka_consumer|kafka_consumer_group' /tmp/coll-metrics.txt | cut -c1-220 | head -20`
23. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python -c " import urllib.request t=urllib.request.urlopen('http://frontend-proxy:10000/config_dump?resource=dynamic_route_configs',timeout=10).read().decode() print(t[:200])" 2>&1 | head -5; kubectl -n otel-demo exec deploy/load-generator  ...`
24. `cd /tmp; tail -2 /tmp/fp-err.txt; kubectl -n otel-demo get deploy frontend-proxy -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{" "}{.image}{"\n"}{range .ports[*]} port {.containerPort} {.name}{"\n"}{end}{range .env[*]} {.name}={.value}{"\n"}{end}{range .volumeMounts[*]} mount {.mount ...`
25. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python -c " import urllib.request t=urllib.request.urlopen('http://frontend-proxy:10000/config_dump',timeout=10).read().decode() open('/dev/stdout','w').write(t)" > /tmp/fp-config.json 2>/tmp/fp-err.txt; wc -c /tmp/fp-config.json; tail -1 / ...`
(7 more commands)
Its closing message: alerts and the accounting alert are fixed. Chatbot and mcp still need a decision from you. **Root cause:** `accounting` was scaled to 0 out-of-band about 15 minutes before the alerts. The Helm chart declares 1 replica, and the replica count was not owned by Helm. Nothing consumed the `orders` topic, so the consumer group went inactive and lag grew. Accounting also stopped receiving traffic, which fired its `NoRequestsReceived` alert. **What I changed:** - `kubectl -n otel-demo scale deployment/accounting --replicas=1`. The rollout succeeded (new pod `accounting-5ffb4bd94f-n8qks`, Running 1/1). ...

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R08

Task: SREGym `liveness_probe_misconfiguration_hotel_reservation`

Fault: The deployment `recommendation` has a misconfigured liveness probe that checks a non-existent health endpoint (`/healthz` on port `8080`), causing Kubernetes to repeatedly kill and restart pods. Pods enter recurrent restart loops with unstable availability and shortened uptime between crashes. Users experience intermittent request failures and latency spikes as endpoints churn during restarts.

Not fully ready after the fault: deployments/recommendation

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: consul.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/consul
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: consul
- User requests: 2970 per round over 2 rounds, 0 with failures (before the fault: 2972 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R09

Task: SREGym `wrong_service_selector_social_network`

Fault: The service `user-service` has a misconfigured selector that adds an incorrect label, so it no longer matches the intended backing pods. The service has zero or insufficient endpoints despite healthy-looking deployments, causing routing failures at the service layer. Users observe connection resets/timeouts and partial outages when requests are sent through this service.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 1024 per round over 5 rounds, 1 with failures (before the fault: 1024 per round over 5 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R10

Task: SREGym `liveness_probe_too_aggressive_astronomy_shop`

Fault: The deployment `aux-service` uses an overly aggressive liveness probe (`initialDelaySeconds=0`, `periodSeconds=1`, `failureThreshold=1`) with `terminationGracePeriodSeconds=0`, so brief startup or transient probe failures trigger immediate pod termination. Pods are recycled too quickly to stabilize, producing repeated restart cycles and readiness instability. Users observe intermittent service drops and elevated error rates during normal traffic.

Not fully ready after the fault: deployments/aux-service

Action:

Every workload that was not fully ready after the fault was deleted: aux-service.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: configmaps/aux-service-script, services/aux-service

## R11

Task: AIOpsLab `scale_pod_zero_social_net-mitigation-1`

Fault: The `user-service` deployment of the Social Network application is scaled to zero replicas.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R12

Task: SREGym `node_conntrack_exhaustion_hotel_reservation`

Fault: The edge-traffic-client deployment is pinned to one worker node and opens many held TCP connections to the rpc-gateway service/deployment. Those connections saturate the victim node's Linux nf_conntrack table, so new connections from that node time out even though Kubernetes Deployments, Pods, Services, and Endpoints still look healthy.

Not fully ready after the fault: deployments/profile

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 2972 per round over 1 round, 0 with failures (before the fault: 2953 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R13

Task: SREGym `feature_flag_latent_bug_hotel_reservation`

Fault: A configuration change set `SEARCH_BACKEND_VERSION` to true in ConfigMap `frontend-runtime-config`, activating a dormant code path compiled into the frontend image. When that path is active and requests arrive, the frontend returns HTTP 500 errors on every hotel search request. All pods remain Running throughout — the failure is visible only at the service level as elevated error rates on the /hotels endpoint. The fix is to revert `SEARCH_BACKEND_VERSION` to false in the ConfigMap and roll out the frontend so the dormant path is no longer activated.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 2972 per round over 2 rounds, 2 with failures (before the fault: 2953 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: configmaps/frontend-runtime-config, deployments/frontend, jobs/wrk2-job

## R14

Task: SREGym `valkey_auth_disruption`

Fault: Valkey authentication is broken by an invalid password configuration, so dependent services cannot establish cache sessions and cart-related operations fail.

Not fully ready after the fault: deployments/cart

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R15

Task: SREGym `operator_overload_replicas`

Fault: The TiDBCluster replica count is set to an extreme value (100,000), overwhelming scheduling and controller reconciliation so only a tiny subset of pods ever becomes ready.

Not fully ready after the fault: statefulsets/basic-tidb

Action:

Every workload that was not fully ready after the fault was scaled to zero replicas: basic-tidb.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: statefulsets/basic-tidb

## R16

Task: SREGym `astronomy_shop_ad_service_failure`

Fault: The `ad` deployment is experiencing persistent internal errors in its ad-serving path, causing repeated request failures. Requests depending on ad responses experience intermittent timeouts. Users see missing or delayed ad content while other storefront paths may still respond. Mechanism: the `flagd-config` ConfigMap in the `astronomy-shop` namespace has the `adFailure` feature flag's `defaultVariant` set to `"on"`, which activates the OpenTelemetry demo's in-app fault path that makes the ad service's `GetAds` gRPC handler return intermittent errors on a fraction of requests.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R17

Task: AIOpsLab `auth_miss_mongodb-mitigation-1`

Fault: MongoDB `url-shorten-mongodb` of the Social Network application is upgraded to require TLS, which breaks the application's connections to it.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 1 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R18

Task: SREGym `astronomy_shop_failed_readiness_probe`

Fault: The cart deployment's readiness probe is consistently failing, so Kubernetes marks pods NotReady and removes them from service endpoints, breaking cart-dependent request paths. Symptoms include repeated readiness probe failures and the cart endpoint disappearing from service discovery. Mechanism: a gRPC readiness probe on port 8080 was patched onto the `cart` deployment, and the `flagd-config` ConfigMap in the `astronomy-shop` namespace has the `failedReadinessProbe` feature flag's `defaultVariant` set to `"on"`, which causes the cart service's gRPC health endpoint to report NOT_SERVING so the probe never succeeds.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R19

Task: SREGym `configmap_drift_hotel_reservation`

Fault: ConfigMap `geo-config` has drifted and is missing required keys (for example `GeoMongoAddress`), so deployment `geo` starts with incomplete runtime config. The service may run but fails dependency initialization and request handling for paths requiring the missing settings. Users observe intermittent errors, degraded responses, and feature-level failures.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 2934 per round over 2 rounds, 1 with failures (before the fault: 2971 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R20

Task: SREGym (HEAD) `astronomy_shop_payment_service_failure`

Fault: The `payment` deployment is returning persistent errors on payment processing requests. Checkout requests reach the payment step but fail authorization/charge flow with retries and eventual cancellation. Users experience failed purchases despite valid cart state. Mechanism: the `flagd-config` ConfigMap in the `astronomy-shop` namespace has the `paymentFailure` feature flag's `defaultVariant` set to `"100%"`, which activates the OpenTelemetry demo's in-app fault path that makes the payment service's `Charge` gRPC handler fail 100% of charge requests.

Action:

An agent worked on the task. Its commands:
1. `kubectl get pods -n astronomy-shop | grep -v Running; kubectl get events -n astronomy-shop --sort-by=.lastTimestamp | tail -15`
2. `kubectl get pods -n astronomy-shop; kubectl get deploy -n astronomy-shop; kubectl rollout history deploy/payment deploy/flagd -n astronomy-shop`
3. `cd /tmp; kubectl get cm -n astronomy-shop | grep -i flag; kubectl get cm flagd-config -n astronomy-shop -o yaml | grep -B2 -A8 '"defaultVariant"' | grep -E '^\s+"[a-zA-Z]+": \{|defaultVariant'; kubectl get deploy payment -n astronomy-shop -o jsonpath='{.spec.template.spec.containers[0].env}'; kubect ...`
4. `cd /tmp; kubectl get cm flagd-config -n astronomy-shop -o json | python3 -I -c ' import json,sys cm=json.load(sys.stdin) k=list(cm["data"])[0] d=json.loads(cm["data"][k]) d["flags"]["paymentFailure"]["defaultVariant"]="off" cm["data"][k]=json.dumps(d,indent=2) json.dump(cm,open("/tmp/cm.json","w"))' ...`
5. `cd /tmp; kubectl get cm flagd-config -n astronomy-shop -o yaml | grep -n -A12 '"paymentFailure"'`
6. `cd /tmp; kubectl get cm flagd-config -n astronomy-shop -o yaml | sed '100s/"100%"/"off"/' | kubectl apply -n astronomy-shop -f - && kubectl rollout restart deploy/flagd -n astronomy-shop && kubectl rollout status deploy/flagd -n astronomy-shop; kubectl get cm flagd-config -n astronomy-shop -o yaml | ...`
Its closing message: **Root cause:** The `paymentFailure` feature flag in the `flagd-config` ConfigMap had `defaultVariant` set to `"100%"`. That made every payment charge fail. The payment logs showed "Payment request failed. Invalid token". All pods were Running, so the fault was in configuration, not in the pods. **Fix:** I changed `paymentFailure.defaultVariant` to `"off"` in the ConfigMap and restarted the `flagd` deployment so it loads the new value. The ConfigMap now shows `off`, and the `flagd` rollout completed. I didn't re-check the payment logs or run a test checkout after the restart, so I haven't seen ...

State after the action:

- In astronomy-shop: Workloads with fewer ready replicas than in the healthy state: none
- In astronomy-shop: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In astronomy-shop: User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: configmaps/flagd-config
- Feature flags changed from the healthy state: none

## R21

Task: SREGym `missing_service_hotel_reservation`

Fault: The Kubernetes Service `mongodb-rate` has been deleted, so dependent workloads can no longer resolve or route traffic to that backend through cluster service discovery. Requests to the service name fail even if pods still exist, creating downstream connection errors and retries. Users observe intermittent failures or complete outages on flows that depend on this service.

Not fully ready after the fault: deployments/rate

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: mongodb-rate
- User requests: 2960 per round over 2 rounds, 2 with failures (before the fault: 2972 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: services/mongodb-rate

## R22

Task: SREGym (HEAD) `operator_wrong_operator_image`

Fault: The TiDB operator controller is rolled to an incorrect image, breaking reconciliation logic and leaving managed TiDB resources in unhealthy or stale states.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n fleetcast get all,pvc,events --sort-by=.lastTimestamp 2>&1 | tail -80`
2. `kubectl -n fleetcast get all,pvc 2>&1; echo ---; kubectl -n fleetcast get events 2>&1 | tail -60`
3. `cd /tmp; kubectl -n fleetcast get deploy fleetcast-satellite-app-backend -o yaml 2>&1 | head -150; echo ---; kubectl -n fleetcast logs deploy/fleetcast-satellite-app-backend --tail=40 2>&1; echo ---; kubectl -n fleetcast describe pod locust-fetcher 2>&1 | head -60`
4. `cd /tmp; kubectl -n fleetcast get rs fleetcast-satellite-app-backend-554685fd7f -o yaml > /tmp/rs-bad.yaml 2>&1; kubectl -n fleetcast get rs fleetcast-satellite-app-backend-db6f7b78b -o yaml > /tmp/rs-good.yaml 2>&1; diff /tmp/rs-bad.yaml /tmp/rs-good.yaml; echo ---; kubectl -n fleetcast get configm ...`
5. `cd /tmp; kubectl -n fleetcast get endpoints,ingress 2>&1; echo ---; kubectl -n fleetcast run hc-$RANDOM --rm -i --restart=Never --image=curlimages/curl:8.7.1 -- sh -c 'curl -s -m 5 -w " HTTP %{http_code}\n" http://fleetcast-satellite-app-backend:5000/api/health; curl -s -m 5 -o /dev/null -w "fronten ...`
6. `cd /tmp; kubectl -n fleetcast logs locust-fetcher --tail=6 2>&1`
Its closing message: The fleetcast app is healthy and I made no changes to the cluster. **What I found** - Both backend and frontend deployments are 2/2 Ready. Both services return 200: the backend's `/api/health` returns `{"status":"ok"}` and the frontend returns 200. Endpoints are populated and the ingress is in place. - The backend had a bad rollout about four minutes before I looked. Its first ReplicaSet (`554685fd7f`) had pods that failed to mount `tidb-ca-configmap` ("failed to sync configmap cache: timed out"), and the readiness probe got connection refused. The deployment then rolled back to the previous R ...

State after the action:

- In tidb-cluster: Workloads with fewer ready replicas than in the healthy state: none
- In tidb-cluster: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In tidb-cluster: User requests: not measured (before the fault: not measured)
- In tidb-operator: Workloads with fewer ready replicas than in the healthy state: deployments/tidb-controller-manager
- In tidb-operator: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In tidb-operator: User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none
- Feature flags changed from the healthy state: none

## R23

Task: SREGym `postgres_lock_contention_product_catalog`

Fault: If one database session holds an ACCESS EXCLUSIVE lock on product-catalog's catalog.products table and doesn't release it,every read of that table queues behind the lock and never completes. product-catalog and the four services that call it (frontend, checkout, recommendation, product-reviews) hangs.The product-catalog pod is Running, and Postgres itself is up with normal CPU and memory, but nothing crashes.The cause is only visible inside the database (pg_locks / pg_stat_activity), which is why it isn’t easy to diagnose the issue.The fix is to end the blocking session (pg_terminate_backend on its pid) or remove the process holding the lock.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: deployments/postgresql

## R24

Task: AIOpsLab `redeploy_without_PV-mitigation-1`

Fault: The namespace of the Hotel Reservation application is deleted without deleting its persistent volumes, and the application is then deployed again.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/consul, deployments/frontend, deployments/geo, deployments/jaeger, deployments/memcached-profile, deployments/memcached-rate, deployments/memcached-reserve, deployments/mongodb-geo, deployments/mongodb-profile, deployments/mongodb-rate, deployments/mongodb-recommendation, deployments/mongodb-reservation, deployments/mongodb-user, deployments/profile, deployments/rate, deployments/recommendation, deployments/reservation, deployments/search, deployments/user
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: consul, frontend, geo, jaeger, jaeger-out, memcached-profile, memcached-rate, memcached-reserve, mongodb-geo, mongodb-profile, mongodb-rate, mongodb-recommendation, mongodb-reservation, mongodb-user, profile, rate, recommendation, reservation, search, user
- User requests: not measured (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R25

Task: AIOpsLab `scale_pod_zero_social_net-mitigation-1`

Fault: The `user-service` deployment of the Social Network application is scaled to zero replicas.

Action:

An agent worked on the task. Its commands:
1. `kubectl get pods,svc -n test-social-network -o wide | head -60`
2. `kubectl get deploy -n test-social-network | grep -i user; kubectl get events -n test-social-network | grep -i user-service | tail`
3. `kubectl scale deploy user-service -n test-social-network --replicas=1; kubectl rollout status deploy/user-service -n test-social-network --timeout=90s`
Its closing message: **Finding:** The `user-service` deployment in `test-social-network` had been scaled to 0 replicas. It had run for about 40 seconds and was then scaled down. No pod was left behind the `user-service` Service, so anything that needed it (register/login, user lookups) would fail. Every other pod was running. **Change:** I ran `kubectl scale deploy user-service --replicas=1`. The rollout finished and the deployment reports its replica available. I only checked the rollout status. I didn't test login or registration, and I didn't look for what scaled it down.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R26

Task: AIOpsLab `user_unregistered_mongodb-mitigation-1`

Fault: A script in the `mongodb-geo` pod of the Hotel Reservation application removes the admin user from MongoDB, and the pods of the service that uses this database are restarted.

Not fully ready after the fault: deployments/geo

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/geo
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: geo
- User requests: 117 per round over 1 round, 1 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R27

Task: SREGym `kubelet_crash`

Fault: A node-level kubelet crash prevents normal pod lifecycle management on the affected node, so rescheduled or restarted workloads fail to come up cleanly and dependent services become partially unavailable.

Not fully ready after the fault: deployments/accounting, deployments/ad, deployments/cart, deployments/checkout, deployments/currency, deployments/email, deployments/flagd, deployments/fraud-detection, deployments/frontend, deployments/frontend-proxy, deployments/grafana, deployments/image-provider, deployments/jaeger, deployments/kafka, deployments/llm, deployments/load-generator, deployments/payment, deployments/postgresql, deployments/product-catalog, deployments/product-reviews, deployments/quote, deployments/recommendation, deployments/shipping, deployments/valkey-cart, statefulsets/opensearch

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R28

Task: SREGym (HEAD) `astronomy_shop_ad_service_high_cpu`

Fault: The `ad` deployment is CPU-throttled due to a tight CPU limit (`100m`) combined with artificially elevated CPU usage, causing sustained high CPU usage and performance degradation. Pods for the ad service show increased request latency and intermittent timeouts under normal storefront traffic. This appears as slower page loads and ad content delays even when other services remain healthy.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n astronomy-shop get pods -o wide 2>&1 | head -100`
2. `kubectl -n astronomy-shop get events --sort-by=.lastTimestamp 2>&1 | tail -50`
3. `kubectl -n astronomy-shop rollout history deployment/ad 2>&1; kubectl -n astronomy-shop rollout history deployment/flagd 2>&1; kubectl -n astronomy-shop get deployment ad -o yaml 2>&1 | head -150`
4. `kubectl -n astronomy-shop get deployment flagd -o yaml 2>&1 | head -200; kubectl -n astronomy-shop get configmap -o name 2>&1`
5. `kubectl -n astronomy-shop get configmap flagd-config -o yaml 2>&1 | head -300`
6. `kubectl -n astronomy-shop logs deploy/ad --tail=40 2>&1; echo ---; kubectl -n astronomy-shop logs deploy/flagd -c flagd --tail=40 2>&1`
7. `kubectl -n astronomy-shop get configmap flagd-config -o json > /tmp/flagd-config.json && python3 -I -c ' import json p="/tmp/flagd-config.json" cm=json.load(open(p)) flags=json.loads(cm["data"]["demo.flagd.json"]) flags["flags"]["adHighCpu"]["defaultVariant"]="off" cm["data"]["demo.flagd.json"]=json ...`
8. `kubectl -n astronomy-shop get configmap flagd-config -o yaml > /tmp/flagd-config.yaml && grep -n '"defaultVariant": "on"' /tmp/flagd-config.yaml; grep -n -A3 '"adHighCpu"' /tmp/flagd-config.yaml`
9. `sed -i '59s/"defaultVariant": "on"/"defaultVariant": "off"/' /tmp/flagd-config.yaml && sed -n '52,60p' /tmp/flagd-config.yaml && kubectl -n astronomy-shop replace -f /tmp/flagd-config.yaml && kubectl -n astronomy-shop rollout restart deployment/flagd && kubectl -n astronomy-shop rollout status deplo ...`
10. `kubectl -n astronomy-shop exec deploy/flagd -c flagd -- sh -c 'grep -A8 "\"adHighCpu\"" /etc/flagd/demo.flagd.json | grep defaultVariant' 2>&1; kubectl -n astronomy-shop rollout restart deployment/ad && kubectl -n astronomy-shop rollout status deployment/ad --timeout=180s && sleep 30 && kubectl -n a ...`
11. `kubectl -n astronomy-shop logs deploy/flagd -c init-config 2>&1 | grep -A8 '"adHighCpu"' | grep defaultVariant; kubectl -n astronomy-shop logs deploy/ad --tail=15 2>&1 | grep -E 'request|problempattern' ; echo; kubectl -n astronomy-shop get pods 2>&1 | grep -vE 'Running' ; echo "non-running check do ...`
Its closing message: The ad service was running with its high-CPU load problem pattern switched on for every request, and I've turned it off. **Root cause:** The `flagd-config` ConfigMap had `adHighCpu` set to `"defaultVariant": "on"`. Every other load-inducing flag defaults to `off`, so this one was the outlier. With it on, the `ad` service burned CPU on every request. The `ad` logs showed "High CPU-Load problempattern enabled" on each call. **Change made:** - Set `adHighCpu` `defaultVariant` to `"off"` in the `flagd-config` ConfigMap, using `kubectl replace`. - Restarted `flagd` so its init container re-copied t ...

State after the action:

- In astronomy-shop: Workloads with fewer ready replicas than in the healthy state: none
- In astronomy-shop: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In astronomy-shop: User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: configmaps/flagd-config
- Feature flags changed from the healthy state: none

## R29

Task: SREGym `trainticket_f17_nested_sql_select_clause_error`

Fault: The voucher service executes malformed nested SQL SELECT logic, which causes query execution failures in the database layer and breaks voucher-related request handling.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 43 per round over 5 rounds, 0 with failures (before the fault: 33 per round over 5 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R30

Task: AIOpsLab `k8s_target_port-misconfig-mitigation-2`

Fault: The Kubernetes Service `text-service` of the Social Network application is given a wrong target port.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R31

Task: SREGym `missing_service_astronomy_shop`

Fault: The Kubernetes Service `ad` has been deleted, so dependent workloads can no longer resolve or route traffic to that backend through cluster service discovery. Requests to the service name fail even if pods still exist, creating downstream connection errors and retries. Users observe intermittent failures or complete outages on flows that depend on this service.

Action:

An agent worked on the task. Its commands:
1. `kubectl get pods -n astronomy-shop | grep -v Running; kubectl get events -n astronomy-shop --sort-by=.lastTimestamp | tail -15`
2. `kubectl get pods -n astronomy-shop; kubectl get events -n astronomy-shop --sort-by=.lastTimestamp | grep -i -E "warn|kill|oom|fail" | tail -20`
3. `kubectl logs -n astronomy-shop deploy/product-catalog --previous --tail=20; kubectl get deploy product-catalog -n astronomy-shop -o yaml | grep -A40 "env:"`
4. `sleep 30; kubectl get pods -n astronomy-shop | grep -E "product|opensearch"; kubectl logs -n astronomy-shop deploy/product-catalog --tail=10; kubectl get pod -n astronomy-shop -l app.kubernetes.io/name=product-catalog -o jsonpath='{.items[0].status.containerStatuses[0].lastState}'`
5. `kubectl get pods -n astronomy-shop | grep -E "product|opensearch"; kubectl logs -n astronomy-shop deploy/product-catalog --tail=10; kubectl get pod -n astronomy-shop -l app.kubernetes.io/name=product-catalog -o jsonpath='{.items[0].status.containerStatuses[0].lastState}'`
6. `kubectl get cm -n astronomy-shop -o yaml | grep -B2 -A3 '"defaultVariant": "on"' | head -40; kubectl get pods -n astronomy-shop | grep -v "1/1\|2/2\|4/4"`
Its closing message: The application is healthy now, but I didn't find a persistent fault to fix, and I changed nothing. - **What happened:** Every workload in `astronomy-shop` was restarted shortly before I looked. The pods were all about a minute old and the earlier pods were being killed. - **Product-catalog:** It crashed twice with exit code 1 while it started. It most likely came up before PostgreSQL and the other dependencies were ready. I couldn't confirm the cause because the container logs came back empty. - **Current state:** Product-catalog has been stable since its second restart. All pods show Ready,  ...

State after the action:

- In astronomy-shop: Workloads with fewer ready replicas than in the healthy state: none
- In astronomy-shop: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: ad
- In astronomy-shop: User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: services/ad
- Feature flags changed from the healthy state: none

## R32

Task: SREGym `expired_tls_hotel_reservation`

Fault: The frontend Ingress resource is configured with a TLS secret named `hotel-frontend-tls` which contains an expired TLS certificate. This breaks HTTPS connections.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: consul.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/consul
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: consul
- User requests: 2934 per round over 2 rounds, 0 with failures (before the fault: 2952 per round over 2 rounds, 1 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R33

Task: SREGym `silent_data_corruption`

Fault: The data files on the persistent volume backing this MongoDB workload were silently corrupted at the storage layer, without any I/O errors being reported. WiredTiger detects the damage as checksum failures when it reads the affected pages, and mongod aborts with a fatal assertion on every start, leaving the database in CrashLoopBackOff until its data is restored.

Not fully ready after the fault: deployments/mongodb-geo

Action:

Every workload that was not fully ready after the fault was scaled to zero replicas: mongodb-geo.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/mongodb-geo
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: mongodb-geo
- User requests: 2952 per round over 2 rounds, 0 with failures (before the fault: 2953 per round over 2 rounds, 1 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R34

Task: SREGym `kubelet_eviction_threshold_misconfig`

Fault: A worker node reports `DiskPressure=True` due to a misconfigured `nodefs.available` eviction threshold in `/var/lib/kubelet/config.yaml`, not actual disk exhaustion — `df` shows ample free space. Deployment `currency` is pinned via `nodeName` to the same node, producing a continuous evict/recreate loop and sustained service unavailability.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: deployments/currency, nodes/kind-worker

## R35

Task: SREGym `liveness_probe_too_aggressive_social_network`

Fault: The deployment `aux-service` uses an overly aggressive liveness probe (`initialDelaySeconds=0`, `periodSeconds=1`, `failureThreshold=1`) with `terminationGracePeriodSeconds=0`, so brief startup or transient probe failures trigger immediate pod termination. Pods are recycled too quickly to stabilize, producing repeated restart cycles and readiness instability. Users observe intermittent service drops and elevated error rates during normal traffic.

Not fully ready after the fault: deployments/aux-service

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 1024 per round over 5 rounds, 0 with failures (before the fault: 1024 per round over 5 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R36

Task: AIOpsLab `astronomy_shop_kafka_queue_problems-mitigation-1`

Fault: The `kafkaQueueProblems` feature flag of the OpenTelemetry demo (astronomy shop) is turned on; the faulty service is `kafka`.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: configmaps/flagd-config

## R37

Task: SREGym `operator_security_context_fault`

Fault: The TiDBCluster pod security context sets an invalid runAsUser value, so workload pods are rejected by policy or fail during startup due to security context violations.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: basic-discovery.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: statefulsets/basic-tidb

## R38

Task: SREGym `liveness_probe_misconfiguration_hotel_reservation`

Fault: The deployment `recommendation` has a misconfigured liveness probe that checks a non-existent health endpoint (`/healthz` on port `8080`), causing Kubernetes to repeatedly kill and restart pods. Pods enter recurrent restart loops with unstable availability and shortened uptime between crashes. Users experience intermittent request failures and latency spikes as endpoints churn during restarts.

Not fully ready after the fault: deployments/recommendation

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 2953 per round over 2 rounds, 1 with failures (before the fault: 2972 per round over 2 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R39

Task: SREGym `operator_invalid_affinity_toleration`

Fault: The TiDBCluster spec contains an invalid toleration effect value, so scheduler constraints cannot be satisfied and TiDB pods remain Pending with unschedulable events.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: basic-discovery.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: statefulsets/basic-tidb

## R40

Task: ITBench `31`

Fault: ITBench fault `ingress-port-blocking-network-policy`, targeting the Deployment `frontend` of the OpenTelemetry demo (namespace `otel-demo`).

Action:

An agent worked on the task. Its commands:
1. `kubectl -n otel-demo get pods -o wide | head -50; kubectl -n otel-demo get events --sort-by=.lastTimestamp | tail -20`
2. `cd /tmp; for d in accounting chatbot mcp; do echo "== $d"; kubectl -n otel-demo logs deploy/$d --tail=15; done; kubectl -n otel-demo get cm,deploy -o name | head -60`
3. `cd /tmp; kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data}' | head -c 4000; echo; kubectl -n otel-demo logs deploy/agent --tail=20; kubectl -n otel-demo get svc,networkpolicy`
4. `cd /tmp; kubectl -n otel-demo get networkpolicy frontend-ingress -o yaml; kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}' | grep -B1 -A0 defaultVariant | grep -v '"off"'; kubectl -n otel-demo logs deploy/load-generator --tail=10; kubectl -n otel-demo get pods --show- ...`
5. `cd /tmp; kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}' | python3 -I -c " import json,sys for k,v in json.load(sys.stdin)['flags'].items(): if v['defaultVariant']!='off': print(k,v['defaultVariant'])" kubectl -n otel-demo get deploy chatbot mcp accounting kafka -o y ...`
6. `cd /tmp; kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}' | tr -d '\n ' | grep -oE '"[A-Za-z]+":\{"defaultVariant":"[^"]+"' | grep -v '"off"'; kubectl -n otel-demo get deploy agent load-generator -o yaml | grep -E -A2 'name: (CHATBOT|MCP|AGENT|LOCUST)[A-Z_]*'; kubectl ...`
7. `cd /tmp; kubectl -n otel-demo get rs -o custom-columns=N:.metadata.name,D:.spec.replicas,C:.metadata.creationTimestamp | grep -E 'agent|chatbot|mcp|account'; kubectl -n otel-demo get deploy agent -o yaml | grep -B2 -A3 -E 'MCP_ENABLED|managedFields' | head -20; kubectl -n otel-demo get deploy -o cus ...`
8. `cd /tmp; kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}' | grep -E '^ "|defaultVariant' | paste - - | grep -v '"off"'; kubectl -n otel-demo get deploy frontend-proxy -o yaml | grep -iE -A1 'chatbot|mcp'; kubectl -n otel-demo get deploy agent -o yaml | grep -B1 -A1 -i ...`
9. `cd /tmp; kubectl -n otel-demo top pods 2>&1 | head -30; kubectl -n otel-demo logs deploy/frontend --tail=15 | cut -c1-250; kubectl -n otel-demo logs deploy/kafka --tail=10 | cut -c1-200; kubectl -n otel-demo logs deploy/chatbot | grep -v httpx | tail`
10. `cd /tmp; kubectl -n otel-demo logs deploy/opamp-server --tail=30 | cut -c1-300; kubectl -n otel-demo get deploy opamp-server -o yaml | grep -A30 'containers:' | head -50`
11. `cd /tmp; kubectl -n otel-demo logs deploy/agent | head -30 | cut -c1-250; kubectl -n otel-demo logs deploy/agent | grep -c 500; kubectl -n otel-demo get deploy agent -o jsonpath='{.spec.template.spec.containers[0].env}' | tr '{' '\n' | grep -E 'name' | cut -c1-150`
12. `cd /tmp; kubectl -n otel-demo set env deploy/agent MCP_ENABLED=True; kubectl -n otel-demo rollout status deploy/agent --timeout=120s; sleep 45; kubectl -n otel-demo logs deploy/mcp --tail=8 | cut -c1-200; kubectl -n otel-demo logs deploy/agent --tail=8 | cut -c1-200`
Its closing message: I fixed the mcp symptom, but I only partly resolved the incident. I couldn't find a cause for the accounting latency or the chatbot alert. **What I found** - The `agent` deployment had `MCP_ENABLED=False`. The agent therefore never called the `mcp` service, which explains `NoRequestsReceived (mcp)`. - The pods were all Running with no restarts. All flagd feature flags were at their default (`off`). - Accounting is consuming Kafka orders normally, and its logs show no errors. - The chatbot is up and idle. I found nothing that sends it traffic. The load generator calls `agent`, not `chatbot`. -  ...

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R41

Task: AIOpsLab `revoke_auth_mongodb-mitigation-2`

Fault: A script in the `mongodb-rate` pod of the Hotel Reservation application revokes admin privileges in MongoDB.

Not fully ready after the fault: deployments/rate

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 58 per round over 1 round, 0 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R42

Task: SREGym `astronomy_shop_payment_service_failure`

Fault: The `payment` deployment is returning persistent errors on payment processing requests. Checkout requests reach the payment step but fail authorization/charge flow with retries and eventual cancellation. Users experience failed purchases despite valid cart state. Mechanism: the `flagd-config` ConfigMap in the `astronomy-shop` namespace has the `paymentFailure` feature flag's `defaultVariant` set to `"100%"`, which activates the OpenTelemetry demo's in-app fault path that makes the payment service's `Charge` gRPC handler fail 100% of charge requests.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none

## R43

Task: SREGym `persistent_volume_affinity_violation`

Fault: Deployment `user-service` requests storage from a PersistentVolume with node affinity on one node set, while its pod `nodeSelector` targets a different node set, creating an unsatisfiable placement. The scheduler reports volume node affinity conflicts and pods remain Pending rather than attaching storage. Users experience service unavailability or severe degradation because stateful pods never become Running.

Not fully ready after the fault: deployments/user-service

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 1024 per round over 5 rounds, 1 with failures (before the fault: 1024 per round over 5 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R44

Task: SREGym `operator_wrong_operator_image`

Fault: The TiDB operator controller is rolled to an incorrect image, breaking reconciliation logic and leaving managed TiDB resources in unhealthy or stale states.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n fleetcast get all,events --sort-by=.lastTimestamp 2>&1 | tail -60`
2. `kubectl -n fleetcast get all 2>&1; kubectl -n fleetcast get events 2>&1 | tail -30`
3. `kubectl -n fleetcast logs deploy/fleetcast-satellite-app-backend --tail=30 2>&1; kubectl -n fleetcast get deploy fleetcast-satellite-app-backend -o yaml | grep -A40 'containers:'; kubectl get pods -A | grep -i tidb`
4. `cd /tmp; kubectl -n fleetcast rollout history deploy/fleetcast-satellite-app-backend; kubectl -n fleetcast get rs -o yaml | grep -E 'image:|name: TIDB|value:' | head -40; kubectl -n fleetcast get ep,ingress; kubectl -n fleetcast logs deploy/fleetcast-satellite-app-backend | grep -iE 'error|fail|exce ...`
5. `cd /tmp; for r in 1 2; do kubectl -n fleetcast rollout history deploy/fleetcast-satellite-app-backend --revision=$r > /tmp/r$r.txt; done; diff /tmp/r1.txt /tmp/r2.txt; kubectl -n fleetcast get deploy fleetcast-satellite-app-backend -o yaml | sed -n '/readinessProbe/,$p' | head -50`
6. `kubectl -n fleetcast get pod locust-fetcher -o yaml | grep -iE -B2 -A2 'http|url|host' | head -30; kubectl -n fleetcast logs locust-fetcher | grep -v JOB | head -5`
Its closing message: I didn't change anything, because the FleetCast application in `fleetcast` is already healthy and I found no fault to fix. - **Pods:** The 2 backend and 2 frontend pods are Running and Ready, with 0 restarts. The service endpoints are populated. - **Backend:** It connects to TiDB (`basic-tidb` in `tidb-cluster`). The logs show contacts and telemetry being written, with no errors. - **TiDB:** The pd, tikv and tidb pods are all Running. - **Backend rollout:** The backend went through one rollout, a `kubectl rollout restart` at <time>. It changed only the restart annotation, not the image or conf ...

State after the action:

- In tidb-cluster: Workloads with fewer ready replicas than in the healthy state: none
- In tidb-cluster: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In tidb-cluster: User requests: not measured (before the fault: not measured)
- In tidb-operator: Workloads with fewer ready replicas than in the healthy state: deployments/tidb-controller-manager
- In tidb-operator: Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- In tidb-operator: User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none
- Feature flags changed from the healthy state: none

## R45

Task: AIOpsLab `revoke_auth_mongodb-mitigation-1`

Fault: A script in the `mongodb-geo` pod of the Hotel Reservation application revokes admin privileges in MongoDB.

Not fully ready after the fault: deployments/geo

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 58 per round over 1 round, 0 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R46

Task: AIOpsLab `redeploy_without_PV-mitigation-1`

Fault: The namespace of the Hotel Reservation application is deleted without deleting its persistent volumes, and the application is then deployed again.

Not fully ready after the fault: none

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 58 per round over 1 round, 0 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: persistentvolumeclaims/geo-pvc, persistentvolumeclaims/profile-pvc, persistentvolumeclaims/rate-pvc, persistentvolumeclaims/recommendation-pvc, persistentvolumeclaims/reservation-pvc, persistentvolumeclaims/user-pvc, services/consul, services/frontend, services/geo, services/jaeger, services/jaeger-out, services/memcached-profile, services/memcached-rate, services/memcached-reserve, services/mongodb-geo, services/mongodb-profile, services/mongodb-rate, services/mongodb-recommendation, services/mongodb-reservation, services/mongodb-user, services/profile, services/rate, services/recommendation, services/reservation, services/search, services/user

## R47

Task: AIOpsLab `revoke_auth_mongodb-mitigation-2`

Fault: A script in the `mongodb-rate` pod of the Hotel Reservation application revokes admin privileges in MongoDB.

Not fully ready after the fault: deployments/rate

Action:

Every workload that was not fully ready after the fault was scaled to zero replicas: rate.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/rate
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: rate
- User requests: 58 per round over 1 round, 1 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R48

Task: AIOpsLab `assign_to_non_existent_node_social_net-mitigation-1`

Fault: The `user-service` deployment of the Social Network application is assigned to a node that does not exist or is not ready.

Action:

An agent worked on the task. Its commands:
1. `kubectl get pods -n test-social-network; kubectl get events -n test-social-network --sort-by=.lastTimestamp | tail -20`
2. `kubectl get deploy user-service -n test-social-network -o yaml | grep -n -B2 -A8 -i -E "affinity|nodeSelector"; kubectl get nodes --show-labels; kubectl rollout history deploy/user-service -n test-social-network`
3. `kubectl patch deploy user-service -n test-social-network --type=json -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'; kubectl rollout status deploy/user-service -n test-social-network --timeout=90s`
Its closing message: **Problem:** The `user-service` pod was stuck in `Pending`. Its deployment had `nodeSelector: kubernetes.io/hostname: extra-node`, and the cluster has no node by that name. The only nodes are `kind-control-plane` and `kind-worker`. The scheduler reported `FailedScheduling` for that reason. **Fix:** I removed the `nodeSelector` from the `user-service` deployment with a JSON patch. The new pod can now schedule on `kind-worker`. `kubectl rollout status` reported the rollout as successful. I didn't re-list the pods or test user operations such as login afterwards.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R49

Task: AIOpsLab `wrong_bin_usage-mitigation-1`

Fault: The `profile` deployment of the Hotel Reservation application is changed to run the `geo` binary instead of `profile`.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: consul.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/consul
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: consul
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R50

Task: AIOpsLab `revoke_auth_mongodb-mitigation-1`

Fault: A script in the `mongodb-geo` pod of the Hotel Reservation application revokes admin privileges in MongoDB.

Not fully ready after the fault: deployments/geo

Action:

Every workload that was not fully ready after the fault was scaled to zero replicas: geo.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/geo
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: geo
- User requests: 58 per round over 1 round, 1 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R51

Task: SREGym `rolling_update_misconfigured_social_network`

Fault: The rollout strategy is set to maxUnavailable=100% and maxSurge=0 while an init container hangs, so an update drains all old replicas before new ones become Ready and the deployment remains stuck with prolonged service unavailability.

Not fully ready after the fault: deployments/custom-service

Action:

Every workload that was not fully ready after the fault was scaled to zero replicas: custom-service.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 1024 per round over 5 rounds, 0 with failures (before the fault: 1024 per round over 5 rounds, 0 with failures)
- Objects the fault changed that still differ from the healthy state: deployments/custom-service

## R52

Task: AIOpsLab `astronomy_shop_kafka_queue_problems-mitigation-1`

Fault: The `kafkaQueueProblems` feature flag of the OpenTelemetry demo (astronomy shop) is turned on; the faulty service is `kafka`.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: accounting.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/accounting
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: configmaps/flagd-config

## R53

Task: AIOpsLab `user_unregistered_mongodb-mitigation-1`

Fault: A script in the `mongodb-geo` pod of the Hotel Reservation application removes the admin user from MongoDB, and the pods of the service that uses this database are restarted.

Not fully ready after the fault: deployments/geo

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 0 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R54

Task: AIOpsLab `misconfig_app_hotel_res-mitigation-1`

Fault: The `geo` deployment of the Hotel Reservation application is changed to a buggy image, `yinfangchen/geo:app3`.

Not fully ready after the fault: deployments/geo

Action:

Every pod in the application's namespace was deleted, so its controllers recreated them.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 117 per round over 1 round, 1 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: deployments/geo

## R55

Task: AIOpsLab `redeploy_without_PV-mitigation-1`

Fault: The namespace of the Hotel Reservation application is deleted without deleting its persistent volumes, and the application is then deployed again.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n test-hotel-reservation get pods -o wide; echo ---; kubectl -n test-hotel-reservation get deploy,svc; echo ---; kubectl -n test-hotel-reservation get events --sort-by=.lastTimestamp | tail -50`
2. `cd /tmp; NS=test-hotel-reservation; kubectl -n $NS get events --field-selector type=Warning -o wide 2>&1 | tail -30; echo ---; kubectl -n $NS get pvc; echo ---; for d in frontend search geo rate profile recommendation reservation user; do echo "== $d"; kubectl -n $NS logs deploy/$d --tail=15 2>&1; d ...`
3. `cd /tmp; NS=test-hotel-reservation; kubectl -n $NS get svc consul -o yaml; echo ---; kubectl -n $NS get endpoints consul -o yaml; echo ---; kubectl -n $NS get deploy consul -o yaml | sed -n '/containers:/,/volumes:/p'; echo ---; kubectl -n $NS logs deploy/consul --tail=30 2>&1`
4. `cd /tmp; NS=test-hotel-reservation; date -u; echo "--- frontend errors (recent)"; kubectl -n $NS logs deploy/frontend --since=5m 2>&1 | grep -ci "error" ; kubectl -n $NS logs deploy/frontend --since=5m 2>&1 | grep -i error | tail -5; echo "--- consul health from frontend pod"; kubectl -n $NS exec de ...`
5. `cd /tmp; NS=test-hotel-reservation; F="kubectl -n $NS exec deploy/frontend --"; $F sh -c 'wget -qO- "http://localhost:5000/hotels?inDate=<time>&outDate=<time>&lat=38.0235&lon=-122.095" | head -c 400'; echo; echo "--- rec"; $F sh -c 'wget -qO- "http://localhost:5000/recommendations?require=dist&lat=3 ...`
6. `cd /tmp; NS=test-hotel-reservation; F="kubectl -n $NS exec deploy/frontend --"; $F sh -c 'wget -S -O- "http://localhost:5000/recommendations?require=dist&lat=38.0235&lon=-122.095" 2>&1 | head -c 600'; echo; echo "--- rate"; $F sh -c 'wget -S -O- "http://localhost:5000/recommendations?require=rate&la ...`
7. `cd /tmp; NS=test-hotel-reservation; F="kubectl -n $NS exec deploy/frontend --"; for r in dis price; do echo "--- $r"; $F sh -c "wget -S -O- 'http://localhost:5000/recommendations?require=$r&lat=38.0235&lon=-122.095' 2>&1 | grep -E 'HTTP/1|hotelId|\"id\"' | head -4"; done; echo "--- consul errors in  ...`
Its closing message: The service is healthy and I made no changes. Here is what I found. **What I found** - All 19 pods in `test-hotel-reservation` were Running and Ready with zero restarts. All six PVCs were Bound, and no Warning events were present. - The only error in the logs was in the frontend: `grpc/lb/consul: error retrieving instances from Consul ... connection refused` for `srv-search`, `srv-profile`, and `srv-recommendation`. All seven of these errors are timestamped <time>–<time>, which is before Consul began serving on port 8500. Consul's logs show frontend health queries succeeding from <time> on, an ...

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: 58 per round over 1 round, 0 with failures (before the fault: 58 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: persistentvolumeclaims/geo-pvc, persistentvolumeclaims/profile-pvc, persistentvolumeclaims/rate-pvc, persistentvolumeclaims/recommendation-pvc, persistentvolumeclaims/reservation-pvc, persistentvolumeclaims/user-pvc, services/consul, services/frontend, services/geo, services/jaeger, services/jaeger-out, services/memcached-profile, services/memcached-rate, services/memcached-reserve, services/mongodb-geo, services/mongodb-profile, services/mongodb-rate, services/mongodb-recommendation, services/mongodb-reservation, services/mongodb-user, services/profile, services/rate, services/recommendation, services/reservation, services/search, services/user

## R56

Task: AIOpsLab `k8s_target_port-misconfig-mitigation-1`

Fault: The Kubernetes Service `user-service` of the Social Network application is given a wrong target port.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: none
- User requests: not measured (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R57

Task: AIOpsLab `k8s_target_port-misconfig-mitigation-2`

Fault: The Kubernetes Service `text-service` of the Social Network application is given a wrong target port.

Not fully ready after the fault: none

Action:

The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to zero replicas: compose-post-service.

State after the action:

- Workloads with fewer ready replicas than in the healthy state: deployments/compose-post-service
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: compose-post-service
- User requests: 15 per round over 1 round, 1 with failures (before the fault: 117 per round over 1 round, 0 with failures)
- Objects the fault changed that still differ from the healthy state: none

## R58

Task: ITBench `114`

Fault: ITBench fault `deleted-kubernetes-service`: the Kubernetes Service `product-catalog` of the OpenTelemetry demo (namespace `otel-demo`) is deleted.

Action:

An agent worked on the task. Its commands:
1. `kubectl -n otel-demo get pods -o wide 2>&1; echo "---"; kubectl -n otel-demo get events --sort-by=.lastTimestamp 2>&1 | tail -40`
2. `kubectl -n otel-demo get deploy,svc,configmap 2>&1; echo "---"; kubectl -n otel-demo get rollout,hpa,ingress 2>&1 | head -30`
3. `kubectl -n otel-demo get configmap flagd-config -o yaml 2>&1`
4. `for d in frontend frontend-proxy accounting; do echo "=== $d ==="; kubectl -n otel-demo logs deploy/$d --tail=25 2>&1; done`
5. `cd /tmp; for d in frontend frontend-proxy accounting flagd; do echo "=== $d errors ==="; kubectl -n otel-demo logs deploy/$d --all-containers --tail=400 2>&1 | grep -iE 'error|fail|warn|timeout|"level":(50|40)|\[(error|warning)\]' | tail -15; done`
6. `kubectl -n otel-demo get deploy accounting frontend frontend-proxy -o yaml 2>&1 | grep -vE 'uid:|resourceVersion|creationTimestamp|generation:|managedFields' | head -400`
7. `cd /tmp; echo "=== kafka ==="; kubectl -n otel-demo logs deploy/kafka --tail=60 2>&1 | tail -60; echo "=== kafka env ==="; kubectl -n otel-demo get deploy kafka -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' 2>&1`
8. `cd /tmp; for p in $(kubectl -n otel-demo get pods -o name); do n=$(basename $p); c=$(kubectl -n otel-demo logs $p --all-containers --tail=300 2>/dev/null | grep -ciE '"level":50|\berror\b|exception|\bfail'); echo "$c $n"; done | sort -rn | head -15`
9. `cd /tmp; for d in recommendation shipping currency; do echo "=== $d ==="; kubectl -n otel-demo logs deploy/$d --tail=300 2>&1 | grep -iE '"level":50|\berror\b|exception|\bfail' | sort | uniq -c | sort -rn | head -6; done`
10. `cd /tmp; kubectl -n otel-demo exec deploy/frontend-proxy -- sh -c 'wget -qO- http://localhost:10000/stats 2>/dev/null || curl -s http://localhost:10000/stats' 2>&1 | grep -E 'upstream_rq_(5xx|4xx|timeout|retry|total)|upstream_cx_connect_fail' | grep -v ': 0$' | head -60`
11. `cd /tmp; kubectl -n otel-demo logs deploy/otel-collector --tail=80 2>&1 | cut -c1-400`
12. `cd /tmp; kubectl -n otel-demo get configmap otel-collector -o jsonpath='{.data.relay}' 2>&1 | head -200; echo; echo "---keys---"; kubectl -n otel-demo get configmap otel-collector -o jsonpath='{.data}' | head -c 300`
13. `cd /tmp; kubectl -n otel-demo logs deploy/otel-collector --tail=2000 2>&1 | grep -E '"otelcol.component.id"|Exporting failed|Dropping|queue is full|dropped' | sed -E 's/"resource": \{[^}]*\}//' | cut -c1-400 | sort | uniq -c | sort -rn | head -15; echo "--- pipelines ---"; kubectl -n otel-demo get c ...`
14. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- sh -c 'for u in / /api/products /api/cart /api/recommendations /api/data; do printf "%s " $u; curl -s -o /dev/null -m 10 -w "%{http_code} %{time_total}s\n" http://frontend-proxy:8080$u; done' 2>&1 | head -30`
15. `cd /tmp; kubectl -n otel-demo get svc -A 2>/dev/null | grep -iE 'jaeger|clickhouse|opensearch|prometheus' ; echo "---"; kubectl get ns 2>&1; echo "--- jaeger endpoint ---"; kubectl get endpoints -A 2>/dev/null | grep 10.96.185.74 ; kubectl get svc -A -o wide 2>/dev/null | grep 10.96.185.74`
16. `cd /tmp; kubectl -n otel-demo exec deploy/load-generator -- python3 -c ' import urllib.request,time for u in ["/","/api/products","/api/cart?sessionId=x","/api/recommendations?productIds=","/api/data","/api/currency"]: t=time.time() try: r=urllib.request.urlopen("http://frontend-proxy:8080"+u,timeou ...`
17. `cd /tmp; kubectl get crd 2>/dev/null | grep -iE 'chaos|istio' | awk '{print $1}' | head -40; echo "--- chaos objects in otel-demo ---"; for r in $(kubectl api-resources --api-group=chaos-mesh.org -o name 2>/dev/null); do kubectl -n otel-demo get $r -o wide 2>&1; done; echo "--- istio objects ---"; k ...`
18. `cd /tmp; kubectl get prometheusrules -A 2>&1 | head; kubectl get prometheusrules -A -o yaml 2>/dev/null | grep -B3 -A12 -E 'HighRequestErrorRate|HighRequestLatency' | head -120; echo "--- chaos cluster-wide ---"; kubectl get podchaos,networkchaos,stresschaos,httpchaos,podhttpchaos,schedules,workflow ...`
19. `cd /tmp; P="/api/v1/namespaces/prometheus/services/prometheus-kube-prometheus-prometheus:9090/proxy/api/v1"; q(){ kubectl get --raw "$P/query?query=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$1")" 2>&1 | python3 -c 'import json,sys d=json.load(sys.stdin) if d.get( ...`
20. `cd /tmp; which jq; P="/api/v1/namespaces/prometheus/services/prometheus-kube-prometheus-prometheus:9090/proxy/api/v1"; kubectl get --raw "$P/alerts" 2>&1 | head -c 3000; echo; echo ==; kubectl get --raw "$P/query?query=sum%20by%20(service_name%2Cstatus_code)%20(rate(traces_span_metrics_calls_total%7 ...`
21. `cd /tmp; PP=$(kubectl -n prometheus get pods -l app.kubernetes.io/name=prometheus -o name 2>/dev/null | head -1); echo "pod: $PP"; kubectl -n prometheus exec $PP -c prometheus -- wget -qO- 'http://localhost:9090/api/v1/alerts' 2>&1 | head -c 2500; echo; echo ==; kubectl -n prometheus exec $PP -c pro ...`
22. `cd /tmp; which curl wget; (kubectl -n prometheus port-forward svc/prometheus-kube-prometheus-prometheus 19090:9090 >/tmp/pf.log 2>&1 &); sleep 4; curl -s 'http://localhost:19090/api/v1/alerts' | head -c 2500; echo; echo ==; curl -s -G 'http://localhost:19090/api/v1/query' --data-urlencode 'query=sum ...`
23. `cd /tmp; pkill -f "port-forward svc/prometheus" 2>/dev/null; P="/api/v1/namespaces/prometheus/services/http:prometheus-kube-prometheus-prometheus:9090/proxy/api/v1"; kubectl get --raw "$P/alerts" 2>&1 | head -c 2500; echo; echo ==; kubectl get --raw "$P/query?query=sum%20by%20(service_name%2Cstatus_ ...`
24. `cd /tmp; kubectl -n otel-demo logs deploy/flagd -c flagd --tail=60 2>&1 | cut -c1-300; echo "=== flagd spec ==="; kubectl -n otel-demo get deploy flagd -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}{.spec.template.spec.containers[0].args}{"\n"}{.spec.template.spec.volumes}{"\n"}' 2>&1`
25. `cd /tmp; kubectl -n otel-demo logs deploy/accounting --tail=400 2>&1 | grep -iE 'warn|fail|error|timeout|slow|lag' | sort | uniq -c | sort -rn | head -10; echo "=== kafka topics via logs ==="; kubectl -n otel-demo logs deploy/kafka --tail=2000 2>&1 | grep -iE 'orders|topic' | tail -8`
(26 more commands)
Its closing message: k about 3 minutes to start listening (its log shows "Ad service starting" at <time> and the Prometheus endpoint at <time>), so requests during that window were refused. The frontend-proxy errors were 5xx responses passed through from the frontend. - **Accounting latency:** The accounting consumer logged `Subscribed topic not available: orders` at startup, before the topic existed. Kafka now has the `orders` topic, and the accounting consumer group has zero lag. I could not confirm a root cause for the latency alert itself, because I couldn't query Prometheus. The likely source is the same star ...

State after the action:

- Workloads with fewer ready replicas than in the healthy state: none
- Services that had ready endpoints in the healthy state and now have none, or a changed selector or ports: product-catalog
- User requests: not measured (before the fault: not measured)
- Objects the fault changed that still differ from the healthy state: none
