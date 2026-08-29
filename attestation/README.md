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
