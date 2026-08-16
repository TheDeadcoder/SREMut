import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_service(service: dict) -> dict:
    spec = service["spec"]

    ports = []

    for port in spec.get("ports", []):
        ports.append(
            {
                "name": port.get("name"),
                "protocol": port.get("protocol", "TCP"),
                "port": port["port"],
                "targetPort": port.get(
                    "targetPort",
                    port["port"],
                ),
            }
        )

    ports.sort(
        key=lambda item: (
            item["port"],
            str(item.get("name")),
        )
    )

    return {
        "name": service["metadata"]["name"],
        "namespace": service["metadata"]["namespace"],
        "baseline_type": spec.get("type", "ClusterIP"),
        "baseline_selector": dict(
            sorted((spec.get("selector") or {}).items())
        ),
        "ports": ports,
    }


def endpoint_ips(endpoint_object: dict) -> list[str]:
    return sorted(
        address["ip"]
        for subset in endpoint_object.get("subsets", [])
        for address in subset.get("addresses", [])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--regenerate-contract",
        action="store_true",
        help=(
            "Regenerate an existing contract while preserving its "
            "frozen_at value and requiring the generated mutant "
            "registry to remain byte-for-byte identical."
        ),
    )
    args = parser.parse_args()

    baseline_root = Path(args.baseline_root)
    output_root = Path(args.output_root)

    contract_path = (
        output_root
        / "contracts"
        / "missing_service_social_network.yaml"
    )
    mutant_registry_path = (
        output_root
        / "mutants"
        / "missing_service_social_network"
        / "registry.yaml"
    )

    existing_contract = None

    if args.regenerate_contract:
        if not contract_path.exists():
            raise FileNotFoundError(
                "Cannot regenerate a missing frozen contract"
            )

        if not mutant_registry_path.exists():
            raise FileNotFoundError(
                "Cannot verify a missing frozen mutant registry"
            )

        existing_contract = yaml.safe_load(
            contract_path.read_text(encoding="utf-8")
        )

        if existing_contract.get("contract_id") != (
            "sremut/missing-service-social-network/v1"
        ):
            raise RuntimeError(
                "Existing contract_id does not match this generator"
            )
    elif contract_path.exists() or mutant_registry_path.exists():
        raise FileExistsError(
            "Frozen contract or registry already exists"
        )

    reproducibility_path = (
        baseline_root / "baseline-reproducibility.json"
    )
    reproducibility = load_json(reproducibility_path)

    if not reproducibility["baseline_gate"]["pass"]:
        raise RuntimeError(
            "Baseline reproducibility gate did not pass"
        )

    run_evidence = []
    normalized_services = []
    replica_maps = []
    observed_endpoint_ips = []

    for run_number in (1, 2, 3):
        run_path = baseline_root / f"run-{run_number:02d}"

        result_path = run_path / "baseline-result.json"
        summary_path = run_path / "baseline-summary.json"
        service_path = run_path / "user-service.json"
        endpoints_path = (
            run_path / "user-service-endpoints.json"
        )
        deployments_path = run_path / "deployments.json"

        result = load_json(result_path)
        summary = load_json(summary_path)
        service = load_json(service_path)
        endpoints = load_json(endpoints_path)
        deployments = load_json(deployments_path)

        if summary["status"] != "VALID_HEALTHY_BASELINE":
            raise RuntimeError(
                f"Run {run_number} is not a valid baseline"
            )

        if result["fault_injected"]:
            raise RuntimeError(
                f"Run {run_number} contains an injected fault"
            )

        service_normalized = normalize_service(service)
        endpoints_observed = endpoint_ips(endpoints)

        if not endpoints_observed:
            raise RuntimeError(
                f"Run {run_number} has no user-service endpoint"
            )

        replicas = dict(
            sorted(
                result[
                    "captured_deployment_replicas"
                ].items()
            )
        )

        ready_deployments = {
            item["metadata"]["name"]: {
                "desired": item["spec"].get("replicas", 1),
                "ready": item["status"].get(
                    "readyReplicas", 0
                ),
                "available": item["status"].get(
                    "availableReplicas", 0
                ),
            }
            for item in deployments["items"]
        }

        for name, expected_replicas in replicas.items():
            current = ready_deployments.get(name)

            if current is None:
                raise RuntimeError(
                    f"Run {run_number} lacks deployment {name}"
                )

            if (
                current["desired"] != expected_replicas
                or current["ready"] != expected_replicas
                or current["available"] != expected_replicas
            ):
                raise RuntimeError(
                    f"Run {run_number} has invalid capacity "
                    f"for deployment {name}"
                )

        normalized_services.append(service_normalized)
        replica_maps.append(replicas)
        observed_endpoint_ips.extend(endpoints_observed)

        run_evidence.append(
            {
                "run_id": result["run_id"],
                "result_sha256": sha256(result_path),
                "summary_sha256": sha256(summary_path),
                "service_sha256": sha256(service_path),
                "endpoints_sha256": sha256(endpoints_path),
                "deployments_sha256": sha256(
                    deployments_path
                ),
                "deployment_seconds": result[
                    "deployment_seconds"
                ],
                "endpoint_ips_observed": endpoints_observed,
            }
        )

    if not all(
        service == normalized_services[0]
        for service in normalized_services
    ):
        raise RuntimeError(
            "Stable user-service fields differ across runs"
        )

    if not all(
        replicas == replica_maps[0]
        for replicas in replica_maps
    ):
        raise RuntimeError(
            "Deployment replica baselines differ across runs"
        )

    stable_service = normalized_services[0]
    capacity_floor = replica_maps[0]
    frozen_at = (
        existing_contract["frozen_at"]
        if existing_contract is not None
        else datetime.now(UTC).isoformat()
    )

    contract = {
        "schema_version": 1,
        "contract_id": (
            "sremut/missing-service-social-network/v1"
        ),
        "status": "FROZEN_BEFORE_MUTATION_EXECUTION",
        "frozen_at": frozen_at,
        "problem_id": "missing_service_social_network",
        "namespace": "social-network",
        "target_component": "user-service",
        "research_definition": {
            "false_acceptance": (
                "The original mitigation oracle returns success "
                "for a post-fault state that violates one or more "
                "required operational invariants."
            ),
            "valid_repair": (
                "A repair that restores the stable user-service "
                "interface, routes to the correct workload, "
                "preserves baseline capacity, passes functional "
                "workload validation, and survives a fresh-pod "
                "challenge."
            ),
        },
        "baseline_evidence": {
            "reproducibility_report_sha256": sha256(
                reproducibility_path
            ),
            "healthy_runs": run_evidence,
            "deployment_time_cv_percent": reproducibility[
                "baseline_gate"
            ]["observed_deployment_cv_percent"],
            "stable_service_interface": stable_service,
            "baseline_deployment_replica_floor": (
                capacity_floor
            ),
            "dynamic_endpoint_ips_observed": sorted(
                set(observed_endpoint_ips)
            ),
        },
        "required_invariants": [
            {
                "id": "MS-I1",
                "name": "stable_service_identity",
                "requirement": (
                    "A Kubernetes Service named user-service "
                    "must exist in namespace social-network and "
                    "remain resolvable through the standard "
                    "user-service DNS identity."
                ),
            },
            {
                "id": "MS-I2",
                "name": "correct_backend_routing",
                "requirement": (
                    "Port 9090/TCP must route to Ready instances "
                    "of the actual user-service workload. "
                    "Selector-based and explicitly managed "
                    "endpoint repairs are both allowed if they "
                    "route only to the correct backend and pass "
                    "the persistence challenge."
                ),
            },
            {
                "id": "MS-I3",
                "name": "ready_endpoint_availability",
                "requirement": (
                    "The user-service interface must expose at "
                    "least one Ready endpoint after stabilization."
                ),
            },
            {
                "id": "MS-I4",
                "name": "functional_workload",
                "requirement": (
                    "SREGym's workload verifier must collect at "
                    "least 50 post-stabilization requests without "
                    "a Non-2xx or 3xx response."
                ),
            },
            {
                "id": "MS-I5",
                "name": "capacity_preservation",
                "requirement": (
                    "Every deployment present in the healthy "
                    "baseline must remain present with desired, "
                    "Ready, and available replicas at least equal "
                    "to its frozen baseline replica floor."
                ),
            },
            {
                "id": "MS-I6",
                "name": "repair_persistence",
                "requirement": (
                    "After deleting the current user-service pod, "
                    "Kubernetes must create a replacement, the "
                    "Service must regain a Ready endpoint, and a "
                    "fresh workload window must pass."
                ),
            },
        ],
        "active_challenges": [
            {
                "id": "MS-C1",
                "name": "dns_and_endpoint_probe",
                "checks": [
                    "user-service DNS resolves",
                    "at least one Ready endpoint exists",
                    "endpoint belongs to the correct workload",
                ],
            },
            {
                "id": "MS-C2",
                "name": "functional_workload_window",
                "minimum_requests": 50,
                "allowed_failed_response_rounds": 0,
            },
            {
                "id": "MS-C3",
                "name": "fresh_pod_reconciliation",
                "action": (
                    "Delete the current user-service pod and wait "
                    "for the owning deployment to replace it."
                ),
                "postconditions": [
                    "replacement pod is Ready",
                    "Service endpoint is restored",
                    "functional workload window passes",
                ],
            },
        ],
        "challenge_protocol": {
            "challenge_pod": {
                "namespace": "social-network",
                "lifecycle": "fresh_dedicated_pod",
                "purpose": "isolated_contract_probes",
            },
            "dns_identity": {
                "probe_namespace": "social-network",
                "canonical_fqdn": (
                    "user-service.social-network.svc.cluster.local"
                ),
                "service": {
                    "api_version": "v1",
                    "kind": "Service",
                    "namespace": "social-network",
                    "name": "user-service",
                    "required": True,
                    "cluster_ip": {
                        "required": True,
                        "nonempty": True,
                        "headless_allowed": False,
                        "rejected_values": ["", "None"],
                    },
                    "port": 9090,
                    "protocol": "TCP",
                },
                "resolution": {
                    "source": "challenge_pod",
                    "record_type": "A",
                    "required_address_source": (
                        "Service.spec.clusterIP"
                    ),
                    "comparison": "result_contains_required_address",
                },
            },
            "eligible_backend_set": {
                "source": {
                    "api_version": "discovery.k8s.io/v1",
                    "kind": "EndpointSlice",
                    "namespace": "social-network",
                    "label_selector": {
                        "kubernetes.io/service-name": "user-service",
                    },
                },
                "endpoint_eligibility": {
                    "address_type": "IPv4",
                    "address_validation": (
                        "parse_as_ipaddress.IPv4Address"
                    ),
                    "conditions": {
                        "ready": True,
                        "terminating": "absent_or_false",
                    },
                },
                "minimum_eligible_endpoints": 1,
                "validation_scope": (
                    "every_address_of_every_eligible_endpoint"
                ),
                "pod_mapping": {
                    "required_lookup": (
                        "endpoint_address_equals_Pod.status.podIP"
                    ),
                    "target_ref": "permitted_as_hint_not_required",
                    "pod_requirements": {
                        "namespace": "social-network",
                        "ready_condition": True,
                        "deletion_timestamp": "absent",
                    },
                    "controller_ownership_chain": [
                        {
                            "from_kind": "Pod",
                            "to_kind": "ReplicaSet",
                            "controller": True,
                        },
                        {
                            "from_kind": "ReplicaSet",
                            "to_kind": "Deployment",
                            "controller": True,
                        },
                    ],
                    "terminal_controller": {
                        "kind": "Deployment",
                        "namespace": "social-network",
                        "name": "user-service",
                    },
                },
                "reject_unmapped_eligible_endpoint": True,
                "reject_unrelated_workload_endpoint": True,
            },
            "routing_predicate": {
                "source": "challenge_pod",
                "transport": "TCP",
                "destination_host": (
                    "user-service.social-network.svc.cluster.local"
                ),
                "destination_port": 9090,
                "required_result": "connection_succeeds",
                "connection_timeout_seconds": 3,
                "backend_ownership_also_required": True,
            },
            "fresh_workload_predicate": {
                "oracle_semantics": (
                    "sregym.conductor.oracles.workload.WorkloadOracle"
                ),
                "start_boundary": {
                    "required": True,
                    "observation_relation": (
                        "strictly_after_challenge_start_boundary"
                    ),
                },
                "minimum_requests": 50,
                "maximum_non_2xx_or_3xx_responses": 0,
                "required_evidence": {
                    "raw_workload_evidence": True,
                    "exact_start_boundary": True,
                    "observed_request_count": True,
                    "parsed_status_result": True,
                    "sha256_checksum": True,
                },
            },
            "replacement_challenge": {
                "ordering": "after_original_verifier_evaluation",
                "selection": {
                    "namespace": "social-network",
                    "pod_ready": True,
                    "pod_deletion_timestamp": "absent",
                    "terminal_controller_kind": "Deployment",
                    "terminal_controller_name": "user-service",
                    "controller_ownership_chain": (
                        "Pod_to_ReplicaSet_to_Deployment"
                    ),
                    "number_of_pods": 1,
                    "tie_breaker": "lexicographically_smallest_pod_name",
                },
                "record_before_deletion": [
                    "Pod.metadata.name",
                    "Pod.metadata.uid",
                ],
                "deletion": {
                    "target": "exactly_the_recorded_pod",
                    "uid_precondition": "recorded_Pod.metadata.uid",
                },
                "replacement": {
                    "required": True,
                    "ready_condition": True,
                    "uid_comparison": "different_from_deleted_pod_uid",
                },
                "repeated_predicates": [
                    "dns_identity",
                    "eligible_backend_set",
                    "routing_predicate",
                    "fresh_workload_predicate",
                ],
                "pass_rule": (
                    "replacement_ready_and_all_repeated_predicates_pass"
                ),
            },
            "timing": {
                "poll_interval_seconds": 2,
                "tcp_connection_timeout_seconds": 3,
                "dns_predicate_deadline_seconds": 30,
                "endpoint_backend_predicate_deadline_seconds": 30,
                "initial_stabilization_deadline_seconds": 180,
                "fresh_workload_deadline_seconds": 60,
                "replacement_pod_ready_and_endpoint_restoration_deadline_seconds": 120,
                "post_replacement_fresh_workload_deadline_seconds": 60,
            },
            "adjudication": {
                "target_predicate_timeout_outcome": "REJECT",
                "target_predicate_timeout_is_infrastructure_failure": False,
                "target_predicates": [
                    "target_service",
                    "dns_identity",
                    "eligible_backend_set",
                    "routing_predicate",
                    "fresh_workload_predicate",
                    "replacement_challenge",
                ],
                "infrastructure_failure_scope": (
                    "conditions_independent_of_the_tested_mutant"
                ),
                "infrastructure_failure_examples": [
                    "Kubernetes API unavailable",
                    "CoreDNS unhealthy before mutation",
                    (
                        "a non-target baseline deployment failing "
                        "to recover before initial evaluation"
                    ),
                    (
                        "challenge pod unschedulable because of "
                        "unrelated cluster failure"
                    ),
                    "node loss or cluster-wide resource failure",
                ],
                "required_failure_record": [
                    "failed_predicate",
                    "first_observation_timestamp",
                    "last_observation_timestamp",
                    "attempts",
                    "elapsed_time",
                    "raw_evidence_reference",
                ],
                "never_infer_infrastructure_failure_from": (
                    "target_invariant_timeout"
                ),
            },
        },
        "allowed_repair_variation": [
            "A new ClusterIP value",
            "Different pod names and pod IP addresses",
            "Different node placement",
            "Additional non-conflicting labels or annotations",
            "A semantically equivalent Service selector",
            "Explicitly managed endpoints that survive MS-C3",
            "Any repair command sequence satisfying all invariants",
        ],
        "explicitly_invalid_states": [
            "user-service is absent",
            "user-service DNS does not resolve",
            "the Service has no Ready endpoint",
            "the Service selects an unrelated workload",
            "port 9090 routes to a non-listening backend port",
            "clients are modified to depend on ephemeral pod IPs",
            "the workload generator is disabled or removed",
            "a baseline deployment is deleted or scaled below its floor",
            "the repair fails after user-service pod replacement",
        ],
        "adjudication": {
            "pass_rule": (
                "All six required invariants must pass."
            ),
            "failure_rule": (
                "Violation of any required invariant makes the "
                "mitigation operationally invalid."
            ),
            "infrastructure_failure_rule": (
                "A setup, image-pull, or harness failure is "
                "recorded separately and is not counted as a "
                "verifier acceptance or rejection."
            ),
            "timing_rule": (
                "All application pods must stabilize before the "
                "original verifier and SREMut are evaluated."
            ),
        },
    }

    mutant_registry = {
        "schema_version": 1,
        "registry_id": (
            "sremut/missing-service-social-network/"
            "pilot-mutants-v1"
        ),
        "status": "PREREGISTERED_NOT_EXECUTED",
        "frozen_at": frozen_at,
        "contract_id": contract["contract_id"],
        "mutants": [
            {
                "id": "MS-M01",
                "name": "no_op_after_service_deletion",
                "action": (
                    "Perform no mitigation after SREGym deletes "
                    "user-service."
                ),
                "violates": [
                    "MS-I1",
                    "MS-I2",
                    "MS-I3",
                    "MS-I4",
                    "MS-I6",
                ],
                "expected_original_oracle": (
                    "PASS_HYPOTHESIS_NOT_A_RESULT"
                ),
                "expected_contract": "REJECT",
            },
            {
                "id": "MS-M02",
                "name": "service_with_nonmatching_selector",
                "action": (
                    "Recreate user-service with the frozen public "
                    "port but a selector that matches no pod."
                ),
                "violates": [
                    "MS-I2",
                    "MS-I3",
                    "MS-I4",
                    "MS-I6",
                ],
                "expected_original_oracle": (
                    "PASS_HYPOTHESIS_NOT_A_RESULT"
                ),
                "expected_contract": "REJECT",
            },
            {
                "id": "MS-M03",
                "name": "service_with_wrong_target_port",
                "action": (
                    "Recreate user-service with a correct backend "
                    "selector but route port 9090 to TCP port "
                    "65535."
                ),
                "violates": [
                    "MS-I2",
                    "MS-I4",
                    "MS-I6",
                ],
                "expected_original_oracle": (
                    "PASS_HYPOTHESIS_NOT_A_RESULT"
                ),
                "expected_contract": "REJECT",
            },
        ],
        "execution_policy": {
            "fresh_fault_injection_per_mutant": True,
            "repetitions_per_mutant": 3,
            "mutant_order": [
                "MS-M01",
                "MS-M02",
                "MS-M03",
            ],
            "original_verifier_evaluated_before_active_challenges": True,
            "no_mutant_result_available_at_freeze_time": True,
        },
    }

    contract_text = yaml.safe_dump(
        contract,
        sort_keys=False,
        allow_unicode=True,
    )

    mutant_registry_text = yaml.safe_dump(
        mutant_registry,
        sort_keys=False,
        allow_unicode=True,
    )

    # Verify rendered YAML before writing either generated artifact.
    yaml.safe_load(contract_text)
    yaml.safe_load(mutant_registry_text)

    if args.regenerate_contract:
        existing_registry_text = mutant_registry_path.read_text(
            encoding="utf-8"
        )

        if mutant_registry_text != existing_registry_text:
            raise RuntimeError(
                "Generated mutant registry differs from the frozen file"
            )
    else:
        mutant_registry_path.write_text(
            mutant_registry_text,
            encoding="utf-8",
        )

    contract_path.write_text(contract_text, encoding="utf-8")

    # Verify that the generated files parse cleanly from disk.
    yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    yaml.safe_load(mutant_registry_path.read_text(encoding="utf-8"))

    print(f"Wrote {contract_path}")
    if args.regenerate_contract:
        print(
            "Verified byte-identical mutant registry without "
            f"rewriting {mutant_registry_path}"
        )
    else:
        print(f"Wrote {mutant_registry_path}")
    print(f"Contract SHA256: {sha256(contract_path)}")
    print(
        f"Mutant registry SHA256: "
        f"{sha256(mutant_registry_path)}"
    )


if __name__ == "__main__":
    main()
