![SREMut-logo](https://ik.imagekit.io/sakib61/SREMut/SREMut.png)
</br>
**SREMut mutation-tests verifiers for AI-generated SRE mitigations.**

The first study targets `missing_service_social_network` in SREGym. It
tests whether a terminal pod/deployment health oracle accepts recoveries
that violate Service discovery, backend routing, functional workload,
capacity, or persistence requirements.

## Scientific rules

- Operational contracts are frozen before mutant execution.
- Original verifier results are collected before SREMut challenges.
- Infrastructure failures are not counted as verifier outcomes.
- Raw evidence is immutable and checksum-protected.
- Exact Kubernetes object equality is not required.
- Semantically distinct valid repairs will be evaluated before the full study.

## Pinned upstream

- SREGym: `ba07faf1a322f9b6d4a279643bb796aa2f36f64b`
- SREGym-applications: `2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8`
