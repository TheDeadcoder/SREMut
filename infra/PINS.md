# Environment

Every run under the new studies uses this environment. `setup_server.sh` builds it on a fresh
Ubuntu 24.04 x86_64 host (run as root); re-running it is safe.

| Component | Version |
|---|---|
| SREGym | `c44b1e54c1436989d47ac6d59785426f8fb9151a` |
| SREGym-applications | `afd5b947fbafefc110e7dc00acd698cb2683dc7f` |
| Kubernetes nodes | v1.32.1, `ghcr.io/sregym/kind-node@sha256:ccab5d6cdca66e0b83a77c2711b641fa788024fe217526980da028ac07385b93` (4 nodes, from SREGym `kind/kind-config.yaml`) |
| CNI | Calico v3.29.3 (SREGym `kind/setup_kind_cluster.sh`) |
| kind | v0.27.0 |
| kubectl | v1.32.1 |
| Helm | v4.3.0 |
| Docker Engine | 29.9.0 |
| uv | 0.12.24 |
| Python | 3.12.3 |
| Host | x86_64, 8 vCPU, 32 GB RAM, Ubuntu 24.04 LTS |

The SREGym commit is the last one that registers all 125 problems. The August results under
`experiments/` used SREGym `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` and are not regenerated.

Host settings applied by the script: `fs.inotify.max_user_instances=1024`,
`fs.inotify.max_user_watches=1048576`, automatic package upgrades disabled, Docker packages held.
