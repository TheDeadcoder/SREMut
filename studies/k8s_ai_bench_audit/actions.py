"""Task-specific actions that stand in for an agent: wrong repairs a verifier should reject and correct
alternatives it should accept. Each is a bash script run in the task's directory after its setup."""

STS = """cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Service
metadata: {name: db, namespace: statefulset-test}
spec: {clusterIP: None, selector: {app: db}}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: db, namespace: statefulset-test}
spec:
  serviceName: db
  replicas: REPLICAS
  selector: {matchLabels: {app: db}}
  template:
    metadata: {labels: {app: db}}
    spec:
      initContainers:
      - name: init-data
        image: busybox
        command: ["sh", "-c", "[ -f /data/test ] || echo -n initial_data > /data/test"]
        volumeMounts: [{name: data, mountPath: /data}]
      containers:
      - name: db
        image: busybox
        command: ["sh", "-c", "sleep infinity"]
        volumeMounts: [{name: data, mountPath: /data}]
  volumeClaimTemplates:
  - metadata: {name: data}
    spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Gi}}}
EOF
"""
STS_THREE = STS.replace("REPLICAS", "3") + "kubectl rollout status statefulset/db -n statefulset-test --timeout=300s\n"

POLICY = """cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: np, namespace: ns1}
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
EGRESS
EOF
"""
NS2_LABELS = "  - to: [{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ns2}}}]\n"
NS2_EXPRESSIONS = ("  - to: [{namespaceSelector: {matchExpressions: "
                   "[{key: kubernetes.io/metadata.name, operator: In, values: [ns2]}]}}]\n")
DNS = "  - ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]\n"
DNS_SPLIT = "  - ports: [{protocol: UDP, port: 53}]\n  - ports: [{protocol: TCP, port: 53}]\n"
PROBES = ("kubectl patch deployment webapp -n orders --type=json -p '["
          '{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe","value":PROBE},'
          '{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe","value":PROBE}]\'\n'
          "kubectl rollout status deployment/webapp -n orders --timeout=180s\n")
POD = ("kubectl run web-server --image=IMAGE -n web-server\n"
       "kubectl wait --for=condition=Ready pod/web-server -n web-server --timeout=180s\n")
BOUND = "kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/storage-pvc -n resize-pv --timeout=180s\n"

ACTIONS = {
    ("create-pod", "exact_image"): ("correct", POD.replace("IMAGE", "nginx")),
    ("create-pod", "latest_tag"): ("correct", POD.replace("IMAGE", "nginx:latest")),
    ("create-pod", "qualified_image"): ("correct", POD.replace("IMAGE", "docker.io/library/nginx")),
    ("create-network-policy", "expected_policy"): ("correct", "kubectl apply -f artifacts/desired-policy.yaml\n"),
    ("create-network-policy", "match_expressions"): ("correct", POLICY.replace("EGRESS\n", NS2_EXPRESSIONS + DNS)),
    ("create-network-policy", "split_dns_rules"): ("correct", POLICY.replace("EGRESS\n", NS2_LABELS + DNS_SPLIT)),
    ("create-network-policy", "allow_all"): ("wrong", POLICY.replace("EGRESS\n", "  - {}\n")),
    ("fix-crashloop", "remove_bad_command"): ("correct", (
        "kubectl patch deployment app -n crashloop-test --type=json -p '["
        '{"op":"remove","path":"/spec/template/spec/containers/0/command"},'
        '{"op":"remove","path":"/spec/template/spec/containers/0/args"}]\'\n')),
    ("fix-crashloop", "sleep_instead"): ("wrong", (
        "kubectl patch deployment app -n crashloop-test --type=json -p '["
        '{"op":"replace","path":"/spec/template/spec/containers/0/command","value":["sleep","infinity"]},'
        '{"op":"remove","path":"/spec/template/spec/containers/0/args"}]\'\n')),
    ("fix-image-pull", "valid_tag"): ("correct", "kubectl set image deployment/app nginx=nginx:latest -n debug\n"),
    ("fix-image-pull", "other_image"): ("wrong", (
        "kubectl patch deployment app -n debug --type=json -p '["
        '{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"busybox"},'
        '{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sleep","infinity"]}]\'\n')),
    ("fix-probes", "valid_paths"): ("correct", PROBES.replace(
        "PROBE", '{"httpGet":{"path":"/","port":80},"initialDelaySeconds":5,"periodSeconds":5}')),
    ("fix-probes", "tcp_probes"): ("correct", PROBES.replace(
        "PROBE", '{"tcpSocket":{"port":80},"initialDelaySeconds":5,"periodSeconds":5}')),
    ("resize-pvc", "expand_claim"): ("correct", BOUND + (
        "kubectl patch storageclass standard -p '{\"allowVolumeExpansion\": true}'\n"
        "kubectl patch pvc storage-pvc -n resize-pv "
        "-p '{\"spec\":{\"resources\":{\"requests\":{\"storage\":\"15Gi\"}}}}'\n")),
    ("resize-pvc", "recreate_claim"): ("correct", BOUND + (
        "kubectl delete pod storage-pod -n resize-pv --wait=true\n"
        "kubectl delete pvc storage-pvc -n resize-pv --wait=true\n"
        "sed 's/10Gi/15Gi/' artifacts/storage-pvc.yaml | kubectl apply -f -\n"
        "kubectl apply -f artifacts/storage-pod.yaml\n")),
    ("resize-pvc", "edit_volume_capacity"): ("wrong", BOUND + (
        "kubectl patch pv \"$(kubectl get pvc storage-pvc -n resize-pv -o jsonpath='{.spec.volumeName}')\" "
        "-p '{\"spec\":{\"capacity\":{\"storage\":\"15Gi\"}}}'\n")),
    ("rolling-update-deployment", "set_image"): ("correct", (
        "kubectl set image deployment/web-app nginx=nginx:1.22 -n rollout-test\n")),
    ("rolling-update-deployment", "delete_and_recreate"): ("wrong", (
        "kubectl delete deployment web-app -n rollout-test --wait=true\n"
        "kubectl create deployment web-app --image=nginx:1.22 --replicas=3 -n rollout-test\n")),
    ("scale-down-deployment", "scale_to_one"): ("correct", (
        "kubectl scale deployment web-service --replicas=1 -n scale-down-test\n")),
    ("statefulset-lifecycle", "scale_down"): ("correct", STS_THREE + (
        "kubectl scale statefulset db --replicas=1 -n statefulset-test\n")),
    ("statefulset-lifecycle", "recreate_with_one"): ("correct", STS_THREE + (
        "kubectl delete statefulset db -n statefulset-test --wait=true\n") + STS.replace("REPLICAS", "1")),
}
