# W3 protocol — H4-lite, second application, SINGLE INSTRUMENT

Registered 2026-08-27, **before the run executed**. Either outcome is reportable, as with
H2 (`PROTOCOL_W1.md`) and H3 (`PROTOCOL_W2.md`).

## H4-lite

> The oracle (`MitigationOracle`) and the injector (`inject_missing_service`) are shared
> across the `missing_service_*` family. If the blindness measured for
> `missing_service_social_network` is a property of the oracle rather than of the
> social-network application, `missing_service_hotel_reservation` should behave the same
> way: `{"success": true}` on an unrepaired system while the functional workload fails.

Both outcomes reportable. A `false` verdict would show the result is application-specific
and would bound the claim accordingly.

## REGISTERED DEVIATION — single instrument

PROTOCOL_G1 requires **both** instruments (the in-process oracle and the SREMut worker)
to return `true` at the healthy gate. **This run uses the in-process oracle only.**

Reason: the SREMut worker hard-pins `EXPECTED_NAMESPACE = "social-network"`
(`SREMut/src/sremut/original_oracle_worker.py:23`, enforced at `:135`, rejecting any other
namespace with `ORIGINAL_ORACLE_INPUT_INVALID` / exit 65). That constant is fixed by the
frozen evidence-capture policy v1.1 and the execution profile; the hard rules forbid
editing frozen pre-registration artifacts, so it is **not relaxed**. This was observed
directly in `w2-hotel-01`, which tripped the two-instrument gate for exactly this reason
while the application itself was healthy (10 rounds, 0/29447 non-2xx, in-process oracle
`{"success": true}`).

Consequence for the result, stated in advance: this run carries **one** independent
measurement of the verdict, not two. It is therefore **weaker evidence than any G1 run**
and must be reported as such. It does not inherit G1's cross-instrument agreement.

The instrument used is the conductor's own call path: `mitigation_oracle.evaluate()`
wrapped in the exact `try/except` of `conductor.py:269-271`.

## Method

- One run, id `w3-hotel-01`, problem `missing_service_hotel_reservation`.
- Noise disabled (`enable_noise=False`), to match G1.
- 60 s null-agent delay (PROTOCOL_G1 R1).
- 2 s state sampler throughout.
- >= 10 complete wrk2 rounds per state where obtainable.
- Three states: HEALTHY / FAULTED / RESTORED.
- Healthy gate: in-process oracle `true` AND zero non-2xx in the healthy window.

R3 (`PROTOCOL_G1.md`) continues to govern classification of any `false` verdict.

## Time box

30 minutes. On overrun or any failure the run is abandoned and reported; it is not
debugged.
