# G0.4 Part A — offline analysis of the faulted-state wrk2 workload

Source: `post/wrk2-job.log`, captured 2026-08-26 from `job/wrk2-job` in namespace
`social-network` while the cluster was in the post-injection (faulted) state of
g02-run-01. 39,550 lines, 2,748,303 bytes, spanning
`2026-08-26T11:02:53.405Z -> 2026-08-26T14:29:44.790Z` (3 h 26 m 51 s).

The fault (deletion of `Service/user-service`) took effect at `11:02:39.972Z`, so the
entire log lies inside the faulted window. READ-ONLY analysis; no cluster contact. The
SREMut workload parser was NOT used.

---

## A1. The verdict-moment round

The treatment oracle ran `11:03:20.021093Z -> 11:03:20.584965Z` (0.564 s).

| Round | Start | End (report) | Requests | Non-2xx | Socket errors | In flight at verdict? |
|---:|---|---|---:|---:|---|---|
| #5 | 11:03:03.713981505Z | 11:03:14.764285406Z | 1024 | **88** | none emitted | no — ended 5.26 s before |
| **#6** | **11:03:15.767428389Z** | **11:03:25.775878696Z** | **1024** | **104** | **none emitted** | **YES — window falls wholly inside** |
| #7 | 11:03:26.778987801Z | 11:03:36.789279747Z | 1024 | 102 | none emitted | no — began 6.19 s after |

Round #6 began 4.254 s before the oracle started and reported 5.191 s after it
finished, so the oracle's entire observation window is contained in round #6's 10 s
test. No `Socket errors` line appears for any of these rounds (or anywhere in the log:
`grep -c "Socket errors"` = 0).

**Citable sentence:**

> During the 0.564 s window in which the oracle returned success, the workload round in
> flight recorded 104 non-2xx responses out of 1024 requests.

Bracketing rounds give 88/1024 immediately before and 102/1024 immediately after, so
the verdict moment is not an outlier.

---

## A2. Time series

Full per-round table: `post/workload-rounds.csv` (1132 data rows; columns `round`,
`start_utc`, `end_utc`, `requests`, `non2xx`, `non2xx_rate`, `read_kb`,
`requests_per_sec`, `aborted`).

Parse summary:

| Quantity | Value |
|---|---:|
| Rounds started (`Running wrk2 on round #N`) | 1132 |
| Completed rounds (emitted a `requests in` summary) | **1126** |
| Aborted rounds (`unable to connect ... Connection refused`) | 5 |
| Rounds emitting a `Socket errors` line | **0** |
| Completed rounds with `non2xx == 0` | **0** |

The 5 aborted rounds are #0-#4, at `11:02:54.521Z - 11:03:02.711Z`, before
`nginx-thrift` finished starting after the injector's namespace-wide pod restart. They
are startup artifacts, not fault symptoms.

### Non-2xx rate across all 1126 completed rounds

| Statistic | Value |
|---|---:|
| n | 1126 |
| min | 6.9336 % (71 / 1024) |
| max | 12.8906 % (132 / 1024) |
| mean | **9.9689 %** |
| median | 9.9609 % |
| stdev | 0.9347 pp |

Request totals per round are 1024 in every round but one (a single round recorded 994).

### Drift

| Quarter | n | Window | Mean rate | Stdev |
|---|---:|---|---:|---:|
| Q1 | 281 | 11:03:03Z -> 11:54:28Z | 9.9548 % | 0.9714 pp |
| Q2 | 281 | 11:54:39Z -> 12:46:02Z | 9.9653 % | 0.9424 pp |
| Q3 | 281 | 12:46:13Z -> 13:37:37Z | 9.9814 % | 0.8398 pp |
| Q4 | 283 | 13:37:48Z -> 14:29:33Z | 9.9741 % | 0.9830 pp |

**The rate is stable, not drifting.** The spread across quarter means is 0.027 pp,
two orders of magnitude smaller than the within-quarter stdev. The failure mode is
steady-state for the full 3.5 hours: it neither heals nor degrades.

---

## A3. Why ~10% and not 100%

### The request mix is fixed at exactly 10% compose-post

SREGym configures the workload at
`sregym/service/apps/social_network.py:24`:

    self.payload_script = TARGET_MICROSERVICES / "socialNetwork/wrk2/scripts/social-network/mixed-workload.lua"

passed to `Wrk2WorkloadManager` at `social_network.py:97-109` with
`url="{placeholder}/wrk2-api/post/compose"` (line 107; the URL is the wrk2 base target,
while the lua script overrides the path per request), rate 100, 3 threads,
3 connections, duration 10 (line 95).

`mixed-workload.lua:111-125` is the request generator:

    111  request = function()
    113      local read_home_timeline_ratio = 0.60
    114      local read_user_timeline_ratio = 0.30
    115      local compose_post_ratio       = 0.10
    117      local coin = math.random()
    118      if coin < read_home_timeline_ratio then
    119        return read_home_timeline()
    120      elseif coin < read_home_timeline_ratio + read_user_timeline_ratio then
    121        return read_user_timeline()
    123      else
    124        return compose_post()

So the generated mix is **60 % read-home-timeline, 30 % read-user-timeline,
10 % compose-post** (`mixed-workload.lua:113-115`).

### Which paths reach user-service

| Share | Endpoint | Backend chain | Reaches user-service? |
|---:|---|---|---|
| 60 % | `/wrk2-api/home-timeline/read` | nginx -> HomeTimelineService (`home-timeline/read.lua:53`) -> PostStorageService + SocialGraphService (`HomeTimelineService.cpp:84,89`) | **No** — see below |
| 30 % | `/wrk2-api/user-timeline/read` | nginx -> UserTimelineService (`user-timeline/read.lua:53`) -> PostStorageService only (`UserTimelineService.cpp:89`) | **No** |
| 10 % | `/wrk2-api/post/compose` | nginx (`wrk2-api/post/compose.lua:40-54`) -> ComposePostService -> user-service | **Yes, unconditionally** |

**Compose-post always calls user-service.** `ComposePostHandler::ComposePost`
(`ComposePostHandler.h:539`) launches four helpers unconditionally at lines 572-583:

    575  auto creator_future =
    576      std::async(std::launch::async, &ComposePostHandler::_ComposeCreaterHelper,
    577                 this, req_id, user_id, username, writer_text_map);

and blocks on the result at line 594 (`post.creator = creator_future.get();`). A grep
for `if|else|switch` across lines 570-600 returns **0** — there is no branch guarding
the call. `_ComposeCreaterHelper` pops a `UserServiceClient` from the pool
(`ComposePostHandler.h:150`), throws `SE_THRIFT_CONN_ERROR` "Failed to connect to
user-service" if the pop fails (151-159), and calls
`user_client->ComposeCreatorWithUserId(...)` at line 166, rethrowing on any exception
(168-174). Every compose-post request therefore depends on user-service resolving.

**The 60 % home-timeline path does NOT reach user-service, despite passing through
SocialGraphService.** `SocialGraphService.cpp:84` does construct a `UserServiceClient`
pool, so the possibility had to be excluded rather than assumed. Method-by-method
analysis of `SocialGraphHandler.h`:

| Method | Lines | Touches `_user_service_client_pool`? |
|---|---|---|
| `Follow` | 103-302 | no |
| `Unfollow` | 303-484 | no |
| `GetFollowers` | 485-622 | **no** |
| `GetFollowees` | 623-767 | no |
| `InsertUser` | 768-823 | no |
| `FollowWithUsername` | 824-897 | YES (839, 851, 855, 861, 874, 878) |
| `UnfollowWithUsername` | 898-977 | YES (913, 925, 929, 935, 948, 952) |

Only the two `*WithUsername` methods use it, and neither is on the read path.
`HomeTimelineHandler.h:126` calls `GetFollowers`, which does not.

### Is ~9.4-10.4 % consistent with the mix?

**Yes — quantitatively, not merely qualitatively.** If exactly the 10 % compose-post
share fails and nothing else does, each round of 1024 requests is a
Binomial(n=1024, p=0.10) draw:

| Quantity | Predicted | Observed |
|---|---:|---:|
| Mean rate | 10.0000 % | **9.9689 %** |
| Mean count | 102.4 / 1024 | **102.1 / 1024** |
| Per-round stdev | **0.9375 pp** | **0.9347 pp** |
| Mean deviation from 10 % | — | -0.0311 pp = **-1.11 SE** |

The observed per-round standard deviation matches the binomial prediction to within
0.003 pp, and the grand mean sits 1.11 standard errors below exactly 10 % — well
inside sampling noise. The observed range (6.93 %-12.89 %) is the expected spread of
1126 binomial draws.

**Conclusion.** The ~10 % failure rate is exactly the compose-post share of the request
mix. It is not a partial or flaky failure: **100 % of the requests that depend on
`user-service` fail, and 0 % of those that do not.** The remaining 90 % succeed because
they never touch the deleted Service. Nothing in the observation is unexplained.

---

## A4. Healthy comparator

| Run | Completed rounds | Lines matching `Non-2xx or 3xx responses` |
|---|---:|---:|
| run-01 | 30 | **0** |
| run-02 | 14 | **0** |
| run-03 | 26 | **0** |
| **Total** | **70** | **0** |

Zero non-2xx across all 70 healthy rounds. This is not incidental — it is enforced as
a hard gate on every healthy baseline, `harness/capture_healthy_baseline.sh:194-208`:

    194  echo "Validating workload output..."
    195
    196  if grep -q "Non-2xx or 3xx responses" "${run_path}/workload.log"; then
    197    echo "ERROR: Workload log contains failed HTTP responses."
    198    exit 1
    199  fi
    200
    201  workload_rounds="$(
    202    grep -c "requests in" "${run_path}/workload.log" || true
    203  )"
    204
    205  if [ "${workload_rounds}" -lt 5 ]; then
    206    echo "ERROR: Fewer than five completed workload rounds."
    207    exit 1
    208  fi

A single non-2xx response anywhere in the log fails the baseline. The healthy standard
is therefore literally zero, and the three frozen baselines meet it.

### Contrast

| State | Rounds | Rounds with non-2xx | Mean non-2xx rate |
|---|---:|---:|---:|
| HEALTHY (run-01/02/03) | 70 | **0 (0 %)** | **0.0000 %** |
| FAULTED (g02-run-01) | 1126 | **1126 (100 %)** | **9.9689 %** |

At the verdict moment, the stock oracle returned `{"success": true}` while the workload
in flight was failing 104 of 1024 requests — a rate that the benchmark's own healthy
baseline gate would reject outright.
