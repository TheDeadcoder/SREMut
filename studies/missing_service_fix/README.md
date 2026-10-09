# Patched missing_service oracle

`missing_service.patch` changes one file of the pinned SREGym (`infra/PINS.md`). It composes the
generic `MitigationOracle` with SREGym's existing `ServiceEndpointMitigationOracle`, and gives each
registered variant the port of the Service its injector deletes:

| Problem | Deleted Service | Port |
|---|---|---|
| `missing_service_social_network` | `user-service` | 9090 |
| `missing_service_hotel_reservation` | `mongodb-rate` | 27017 |
| `missing_service_astronomy_shop` | `ad` | 8080 |

Upstream uses the same composition and a fixed port for its own missing-service problem on a newer
application. The stock oracle's behaviour on these problems comes from the registry sweep.

## Prediction

With the patch, each variant passes upstream's validator (`LIFECYCLE_PASS`) in every attempt: the
oracle reports failure while the Service is absent and success after recovery. At the first failing
check, the generic child reports success and the Service-aware child reports failure.

## Attempts

Each attempt runs all three problems: attempts 1 and 3 on server A, attempt 2 on server B.
Attempts are classified as in `studies/registry_sweep/README.md`.

## Running

Once per server, create the patched checkout next to the unmodified one:

```bash
cp -a ~/SREGym ~/SREGym-fix && rm -rf ~/SREGym-fix/.venv
git -C ~/SREGym-fix apply ~/SREMut/studies/missing_service_fix/missing_service.patch
cd ~/SREGym-fix && uv sync --python /usr/bin/python3.12
```

Then, from the repository root:

```bash
python3 studies/missing_service_fix/run_fix.py --attempt 1 --server A
python3 studies/missing_service_fix/fix_ledger.py
python3 studies/missing_service_fix/fix_ledger.py --check
```

`run_fix.py` refuses a checkout without the patch and writes attempts to `runs/` in the same layout
as the registry sweep.
