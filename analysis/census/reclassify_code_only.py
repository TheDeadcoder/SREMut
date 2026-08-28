"""A1: re-derive perturbed kinds from CODE IDENTIFIERS ONLY, by AST.

Excludes every ast.Constant (so all string literals, including docstrings and
kubectl command strings), and comments (never in the AST). Keeps ast.Name ids,
ast.Attribute attrs, and call function names.

Emitted as a SECOND column. The existing classification is not overwritten.

Caveat recorded up front: SREGym injects faults largely through
kubectl.exec_command("kubectl delete service ...") — the evidence lives in string
literals. Excluding literals therefore removes genuine signal as well as prose.
This column is deliberately conservative: it is a lower bound on attributable
kinds, and its purpose is to identify which rows need hand-verification (A3),
not to replace the classification.
"""
from __future__ import annotations
import ast, json, re, sys
from pathlib import Path

SREGYM = Path("/home/sakibbuet2k19/sremut/SREGym")
PROB = SREGYM / "sregym/conductor/problems"
FAULT = SREGYM / "sregym/generators/fault"

KINDS = [
 ("Service",        r'namespaced_service|patch_service|get_service_json|get_service|delete_service'),
 ("Deployment",     r'namespaced_deployment|get_deployment|scale_deployment|patch_deployment|trigger_rollout'),
 ("Pod",            r'namespaced_pod|delete_service_pods|list_pods|delete_pod'),
 ("ConfigMap",      r'config_map|configmap'),
 ("Secret",         r'secret'),
 ("NetworkPolicy",  r'network_polic'),
 ("PVC/PV",         r'persistent_volume|pvc'),
 ("Node",           r'node_exec|docker_exec|patch_node|list_nodes|cordon|drain|taint'),
 ("StatefulSet",    r'stateful_set'),
 ("Ingress",        r'ingress'),
 ("CRD/Operator",   r'custom_object|tidbcluster'),
 ("Job/CronJob",    r'cron_job|namespaced_job|delete_job|create_job'),
 ("RBAC",           r'role_binding|cluster_role|service_account'),
 ("ResourceQuota",  r'resource_quota|limit_range'),
 ("Webhook",        r'webhook'),
 ("container_cmd_env", r'_modify|_change_|env_var|set_env'),
 ("iptables/net",   r'iptables|conntrack|tc_qdisc'),
]

def code_identifiers(node) -> str:
    """Every Name id / Attribute attr / call func name. No Constants at all."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant):
            continue
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
    return " ".join(out)

def index_injectors() -> dict:
    inj = {}
    for f in sorted(FAULT.rglob("*.py")):
        try: tree = ast.parse(f.read_text())
        except Exception: continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("inject_"):
                inj[n.name] = {"file": str(f.relative_to(SREGYM)), "lineno": n.lineno,
                               "ids": code_identifiers(n)}
    return inj

def index_problem_classes() -> dict:
    out = {}
    for f in sorted(PROB.rglob("*.py")):
        try: tree = ast.parse(f.read_text())
        except Exception: continue
        for c in ast.walk(tree):
            if isinstance(c, ast.ClassDef):
                fts = set()
                for n in ast.walk(c):          # fault_type= is a Constant; read it explicitly
                    if isinstance(n, ast.keyword) and n.arg == "fault_type" \
                       and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                        fts.add(n.value.value)
                out[c.name] = {"file": str(f.relative_to(SREGYM)), "lineno": c.lineno,
                               "ids": code_identifiers(c), "fault_types": sorted(fts)}
    return out

def kinds_of(text: str) -> list[str]:
    return sorted({k for k, p in KINDS if re.search(p, text, re.I)})

def main() -> int:
    inj = index_injectors()
    cls = index_problem_classes()
    reg_by_class = json.loads(Path(sys.argv[1]).read_text())   # problem_id -> class name
    res = {}
    for pid, cname in reg_by_class.items():
        c = cls.get(cname)
        if not c:
            res[pid] = {"kinds_code_only": [], "resolved": False, "note": "class not found by AST"}
            continue
        text = c["ids"]
        cites = []
        for ft in c["fault_types"]:
            d = inj.get(f"inject_{ft}")
            if d:
                text += " " + d["ids"]
                cites.append(f"inject_{ft}@{d['file']}:{d['lineno']}")
        res[pid] = {"kinds_code_only": kinds_of(text), "resolved": True,
                    "class": cname, "class_cite": f"{c['file']}:{c['lineno']}",
                    "fault_types": c["fault_types"], "injector_cites": cites}
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
