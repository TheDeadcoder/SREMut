# Oracle history

How each registered problem's mitigation verdict changed over SREGym's history, and when a functional
check was attached to it or detached from it.

## Method

`mine_history.py` reads the registry at the pinned commit (`infra/PINS.md`) and, for each problem,
follows the file of its problem class back through the history, renames included. At every commit
that touched the file it parses the class and records the oracle classes assigned to
`mitigation_oracle` and `resolution_oracle`. A change in either set is an event.

A functional check is an oracle that observes the service as users see it:

| Oracle | Kind |
|---|---|
| `WorkloadOracle` | workload |
| `AlertOracle` | alert |
| `ServiceEndpointMitigationOracle` | connectivity |

A problem lost a functional check if its mitigation verdict carried one at some commit and does not
at the pin. Commits are identified by hash and ordered by their position in the history of the pin.

The miner follows the class named in the registry at the pin. Oracles assigned in a base class or
through a helper function, and classes renamed before the pin, are not seen.

## Outputs

| File | Content |
|---|---|
| `timeline.json` | per problem: class, file, retired flag, functional checks lost, and every change |
| `events.csv` | one row per change of an oracle slot, with functional checks attached or detached |
| `summary.json` | counts, and the commits that attached or detached functional checks |

## Running

On a host with a full SREGym clone:

```bash
python3 studies/oracle_history/mine_history.py --sregym ~/SREGym --out studies/oracle_history
```
