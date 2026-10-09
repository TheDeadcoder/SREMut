"""Mines SREGym's history for every change to each registered problem's mitigation oracle."""
import argparse
import ast
import csv
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = "sregym/conductor/problems/registry.py"
PIN = "c44b1e54c1436989d47ac6d59785426f8fb9151a"
RETIREMENT_COMMIT = "a90b43cc973158120119a9f388081eac4f7adb36"
FUNCTIONAL = {"WorkloadOracle": "workload", "AlertOracle": "alert",
              "ServiceEndpointMitigationOracle": "connectivity"}
SLOTS = ("mitigation_oracle", "resolution_oracle")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def registry_entries(source):
    tree = ast.parse(source)
    registry = max((n for n in ast.walk(tree) if isinstance(n, ast.Dict)), key=lambda d: len(d.keys))
    entries = {}
    for key, value in zip(registry.keys, registry.values):
        if isinstance(value, ast.Lambda):
            value = value.body
        if isinstance(value, ast.Call):
            value = value.func
        if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
            entries[key.value] = value.id
    return entries


def class_modules(source):
    modules = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sregym."):
            for alias in node.names:
                modules[alias.asname or alias.name] = node.module.replace(".", "/") + ".py"
    return modules


def oracle_names(node):
    names = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        func = call.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name and name.endswith("Oracle") and name != "CompoundedOracle":
            names.add(name)
    return names


def class_oracles(source, class_name):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    cls = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    if cls is None:
        return None
    slots = {slot: set() for slot in SLOTS}
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in slots:
                    slots[target.attr] |= oracle_names(node.value)
    return {slot: sorted(names) for slot, names in slots.items()}


def file_history(repo, path):
    out = git(repo, "log", "--follow", "--name-status", "--format=commit %H", PIN, "--", path)
    history, commit = [], None
    for line in out.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line and commit:
            parts = line.split("\t")
            history.append((commit, parts[-1]))
            commit = None
    return list(reversed(history))


def timeline(repo, path, class_name, order):
    changes, previous = [], None
    for commit, path_then in file_history(repo, path):
        try:
            source = git(repo, "show", f"{commit}:{path_then}")
        except subprocess.CalledProcessError:
            continue
        oracles = class_oracles(source, class_name)
        if oracles is None or oracles == previous:
            continue
        changes.append({"commit": commit, "order": order.get(commit), "path": path_then, **oracles})
        previous = oracles
    return changes


def events(problem_id, changes):
    rows = []
    for before, after in zip([{"mitigation_oracle": [], "resolution_oracle": []}] + changes, changes):
        for slot in SLOTS:
            added = sorted(set(after[slot]) - set(before[slot]))
            removed = sorted(set(before[slot]) - set(after[slot]))
            if added or removed:
                rows.append({
                    "problem_id": problem_id, "commit": after["commit"], "order": after["order"], "slot": slot,
                    "added": " ".join(added), "removed": " ".join(removed),
                    "functional_added": " ".join(sorted(FUNCTIONAL[o] for o in added if o in FUNCTIONAL)),
                    "functional_removed": " ".join(sorted(FUNCTIONAL[o] for o in removed if o in FUNCTIONAL)),
                })
    return rows


def lost_functional(changes):
    ever = {FUNCTIONAL[o] for c in changes for o in c["mitigation_oracle"] if o in FUNCTIONAL}
    final = {FUNCTIONAL[o] for o in (changes[-1]["mitigation_oracle"] if changes else []) if o in FUNCTIONAL}
    return sorted(ever - final)


def by_commit(rows, field, subjects):
    counts = {}
    for row in rows:
        if row["slot"] == "mitigation_oracle":
            for kind in row[field].split():
                entry = counts.setdefault(row["commit"], {"subject": subjects.get(row["commit"]), "problems": {}})
                entry["problems"][kind] = entry["problems"].get(kind, 0) + 1
    return counts


def summarize(timelines, rows, subjects):
    lost = sorted(pid for pid, t in timelines.items() if t.get("lost_functional"))
    return {
        "problems": len(timelines),
        "events": len(rows),
        "lost_functional": len(lost),
        "lost_workload": sum("workload" in timelines[p]["lost_functional"] for p in lost),
        "lost_functional_retired": sum(bool(timelines[p]["retired"]) for p in lost),
        "retired": sum(bool(t.get("retired")) for t in timelines.values()),
        "detached_by_commit": by_commit(rows, "functional_removed", subjects),
        "attached_by_commit": by_commit(rows, "functional_added", subjects),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()

    registry = git(args.sregym, "show", f"{PIN}:{REGISTRY}")
    entries, modules = registry_entries(registry), class_modules(registry)
    retired = set(entries) - set(registry_entries(git(args.sregym, "show", f"{RETIREMENT_COMMIT}:{REGISTRY}")))
    order = {sha: i for i, sha in enumerate(git(args.sregym, "rev-list", "--reverse", PIN).split())}

    timelines, rows, cache = {}, [], {}
    for pid, class_name in sorted(entries.items()):
        path = modules.get(class_name)
        if path is None:
            timelines[pid] = {"class": class_name, "path": None, "changes": []}
            continue
        key = (path, class_name)
        if key not in cache:
            cache[key] = timeline(args.sregym, path, class_name, order)
        changes = cache[key]
        timelines[pid] = {"class": class_name, "path": path, "retired": pid in retired,
                          "lost_functional": lost_functional(changes), "changes": changes}
        rows += events(pid, changes)

    commits = {r["commit"] for r in rows}
    subjects = {sha: git(args.sregym, "log", "-1", "--format=%s", sha).strip() for sha in commits}
    summary = summarize(timelines, rows, subjects)
    (args.out / "timeline.json").write_text(json.dumps(timelines, indent=2, sort_keys=True) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with open(args.out / "events.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["problem_id", "commit", "order", "slot", "added", "removed",
                                               "functional_added", "functional_removed"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["problem_id"], r["order"] or 0, r["slot"])))
    print(f"{summary['problems']} problems, {summary['events']} events, {summary['lost_functional']} lost a "
          f"functional check ({summary['lost_workload']} a workload check), "
          f"{summary['lost_functional_retired']} of them later retired")


if __name__ == "__main__":
    main()
