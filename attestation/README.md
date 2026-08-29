# Attestation — PREREGISTRATION_MS_MUTANTS.md

## What was timestamped

The **SHA-256 of the file `PREREGISTRATION_MS_MUTANTS.md`**, not a commit, not a
tree, not a tag:

```
75cd66ededb63fec896c56e8a21c094425ee5ea9b23b5cdece8654becfd1be04  PREREGISTRATION_MS_MUTANTS.md
```

That digest appears verbatim as the `Message data` field inside `prereg.tsr`, which
is what binds the token to these exact bytes. Any edit to the document — one
character — breaks the binding.

## Which authority answered, and when

| | |
|---|---|
| Authority | Free TSA (`https://freetsa.org/tsr`) |
| TSA identity | `O=Free TSA, OU=TSA, CN=www.freetsa.org, L=Wuerzburg, ST=Bayern, C=DE` |
| Status | `Granted` |
| Policy OID | `tsa_policy1` |
| Hash algorithm | sha256 |
| Serial number | `0x0758C88F` |
| **Stated time** | **`Aug 29 09:08:34 2026 GMT`** |
| Host clock at the time (`date -u`) | `Sat Aug 29 09:08:39 UTC 2026` |

The two agree to within 5 seconds, which is the elapsed time between the HTTP
response and the subsequent `date -u`. The point of the exercise is that the
stated time was produced by freetsa.org, not by this host.

## Files

| File | What it is |
|---|---|
| `prereg-sha256.txt` | the digest above, in `sha256sum -c` format |
| `prereg.tsq` | the RFC 3161 timestamp *query* (69 bytes), built with `-cert` |
| `prereg.tsr` | the RFC 3161 timestamp *reply* (4644 bytes) returned by freetsa.org |

## How to re-verify

```bash
sha256sum -c attestation/prereg-sha256.txt
openssl ts -reply -in attestation/prereg.tsr -text
```

The first command proves the document still has the digest that was timestamped.
The second **parses and prints** the token — including `Time stamp:` and
`Message data:` — but it does *not* check the signature.

For a full cryptographic verification, obtain Free TSA's root certificate
independently from `https://freetsa.org/root_ca.pem` and confirm its SHA-256
fingerprint is:

```
A6:37:9E:7C:EC:C0:5F:AA:3C:BF:07:60:13:D7:45:E3:27:BB:BA:A3:8C:0B:9A:F2:24:69:D4:70:1D:18:AA:BC
```

then:

```bash
openssl ts -verify -in attestation/prereg.tsr \
                   -queryfile attestation/prereg.tsq \
                   -CAfile root_ca.pem
```

This was run at creation time and returned `Verification: OK`. Note that the
token embeds its own signing certificate and that self-signed root (because the
query used `-cert`), so verifying against the *embedded* root is circular; the
root must come from freetsa.org and be checked by fingerprint for the
verification to mean anything. Free TSA's root is not in the Ubuntu system CA
bundle, so verifying against `/etc/ssl/certs/ca-certificates.crt` fails with
`self-signed certificate in certificate chain` — that is expected, not a defect.

## What this establishes, and what it does not

**This establishes only that the bytes of `PREREGISTRATION_MS_MUTANTS.md` existed
at the stated time; it establishes nothing whatsoever about the historical runs
recorded in `experiments/RESULT_LEDGER.json`, which remain — as
`analysis/PREREGISTRATION_TIMELINE.md` states — exploratory evidence with no
independently corroborated pre-execution artifact.**

It also does not establish that the cluster was untouched before that time, that
no mutant had been applied, or that any earlier draft of this document said the
same thing.

## Deviation from the document's own Section 9

`PREREGISTRATION_MS_MUTANTS.md` §9.2 describes taking the RFC 3161 timestamp
"over the commit SHA of this document" and committing the reply as
`attestation/PREREGISTRATION_MS_MUTANTS.<sha>.tsr`. What was actually done is
stronger in one respect and different in another: the timestamp is over the
**document's content digest** rather than a commit SHA, and it was taken **before**
the commit existed, so it does not depend on the commit at all. The file is named
`prereg.tsr`. Recorded here rather than corrected in the frozen text.

---

# Second token — the pre-execution tree

## What was timestamped

The **SHA-256 of `attestation/preexec-commit.txt`**, a file whose entire content is the
commit SHA of the pre-execution tree plus a newline:

```
0094d98a0dba781ecc8b304f9980ab0752498deb
```

```
939977c3195e6ac3fcb09a4cdb379a7804ba5d1533766596e9e21901edd51e4a  attestation/preexec-commit.txt
```

That digest is the `Message data` field inside `preexec-commit.tsr`.

## Which authority answered, and when

| | |
|---|---|
| Authority | Free TSA (`https://freetsa.org/tsr`) |
| Status | `Granted` |
| Policy OID | `tsa_policy1` |
| Serial number | `0x07590D55` |
| **Stated time** | **`Aug 29 10:15:38 2026 GMT`** |
| Host clock at the time (`date -u`) | `Sat Aug 29 10:15:46 UTC 2026` |

`openssl ts -verify` against Free TSA's root returned `Verification: OK`.

## Why this is broader than Section 9.2 asked for

`PREREGISTRATION_MS_MUTANTS.md` §9.2 describes a timestamp "over the commit SHA of this
document". This token does that, and because a git commit SHA is a hash over the whole
tree, it attests to more than the document: at `0094d98a` the tree contained the
pre-registration, the frozen contract, the mutant registry, the frozen execution profile
and evidence policies, **and** the two driver files
(`experiments/mutant_run.py`, `experiments/contract_check.py`) in exactly the form used
for the runs. So the deviation recorded in the first token's section is now closed, in a
stronger form than the document anticipated.

The first token binds the document's bytes; this one binds the tree those bytes sit in.
Neither replaces the other.

## What this establishes, and what it does not

**It establishes that this tree existed at the stated time. It establishes nothing about
the cluster** — not that it was healthy, not that it was untouched, not that no mutant had
been applied. It says nothing about the historical runs in
`experiments/RESULT_LEDGER.json`, whose status is unchanged.

One exclusion worth stating: `experiments/smoke-00/healthy/probe-pod.json` was untracked
at `0094d98a` and is therefore **not** covered by this token. Every file that the mutant
runs depend on is covered.

## How to re-verify

```bash
sha256sum attestation/preexec-commit.txt
git rev-parse 0094d98a0dba781ecc8b304f9980ab0752498deb
openssl ts -reply -in attestation/preexec-commit.tsr -text
openssl ts -verify -in attestation/preexec-commit.tsr \
                   -queryfile attestation/preexec-commit.tsq \
                   -CAfile root_ca.pem
```

The root certificate and its expected fingerprint are given in the first token's section.

---

# Third token — the fix-oracle pre-registration

## What was timestamped

The **SHA-256 of `PREREGISTRATION_FIX_ORACLE.md`**, the pre-registration for the study
that executes `analysis/PR_PLAN.md`'s never-tested prediction about
`ServiceEndpointMitigationOracle`:

```
73e1af2079c789b7f533b5477185808138e7312c5a25b1f70bb9c2bc261d67a0  PREREGISTRATION_FIX_ORACLE.md
```

That digest is the `Message data` field inside `prereg-fix-oracle.tsr`.

## Which authority answered, and when

| | |
|---|---|
| Authority | Free TSA (`https://freetsa.org/tsr`) |
| Status | `Granted` |
| Policy OID | `tsa_policy1` |
| Serial number | `0x075A69B3` |
| **Stated time** | **`Aug 29 15:16:02 2026 GMT`** |
| Host clock at the time (`date -u`) | `Sat Aug 29 15:16:02 UTC 2026` |

`openssl ts -verify` against Free TSA's root returned `Verification: OK`.

## Ordering, which is the point

This token was taken **before `experiments/fix_oracle_run.py` existed** — verified at the
time by `ls`, which reported the file absent. The predicted 4x3 verdict matrix in §3 of the
document therefore predates not only the runs but the driver that produces them.

A second token over the pre-execution commit SHA follows, before the first run, exactly as
for the mutant matrix.

## Files

| File | What it is |
|---|---|
| `prereg-fix-oracle-sha256.txt` | the digest above, in `sha256sum -c` format |
| `prereg-fix-oracle.tsq` | the RFC 3161 timestamp query, built with `-cert` |
| `prereg-fix-oracle.tsr` | the RFC 3161 reply returned by freetsa.org |

## How to re-verify

```bash
sha256sum -c attestation/prereg-fix-oracle-sha256.txt
openssl ts -reply -in attestation/prereg-fix-oracle.tsr -text
openssl ts -verify -in attestation/prereg-fix-oracle.tsr \
                   -queryfile attestation/prereg-fix-oracle.tsq \
                   -CAfile root_ca.pem
```

The root certificate and its expected fingerprint are in the first token's section.

## What this establishes, and what it does not

**It establishes that the bytes of `PREREGISTRATION_FIX_ORACLE.md` existed at the stated
time, and nothing else.** It says nothing about the cluster, nothing about whether any
oracle configuration had already been tried informally, and nothing about the nine mutant
records or the thirteen historical runs.
