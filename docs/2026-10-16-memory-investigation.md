# OpenReLife native memory investigation — 2026-10-16

The original Python backend had an abnormal 32–34 GiB physical footprint. Follow-up
heap inspection and controlled tests identified three accumulating paths: native
Vision buffers/results, MPS inference graphs for variable-shaped embedding inputs,
and smaller native window-metadata temporaries. Version 1.1.2 fixes these paths,
has been built and installed, with post-restart measurements recorded below. Historical
untyped allocations cannot all be assigned to an individual call site without
allocation stack logging; this report distinguishes that limitation from defects
which were reproduced and corrected.

## Monitoring closed at the user's request

Both monitors were stopped normally at 18:02 on 2026-10-16 and their launchd jobs
were unloaded. OpenReLife was left running. No additional monitoring is scheduled.
All raw samples and reports are retained in `logs/memory-monitor-20261016-123658/`;
the status files explicitly record `stopped_by_user`, and `closeout.json` contains
the final aggregates.

The app observation covers 5.42 hours, with 320 valid samples, three failed
measurement attempts, one continuous backend identity and no backend restarts.
Median backend footprint is 1.71 GiB; the final sample is 2.64 GiB. Temporary
increases and allocator-retained pages occurred, but the original 32–34 GiB
accumulation was not reproduced during this observation. This is bounded-duration
evidence, not a proof that every possible long-running workload is leak-free.

All 36 system-memory checks reported normal OS pressure. System swap occupancy
declined from 5.92 to 3.64 GiB over the RAM monitoring interval. Existing app
settings, recordings and installed version 1.1.2 were preserved at closeout.

## Extended observation requested after the fix

An eight-hour read-only monitor started at 2026-10-16 12:37:07 Europe/Rome and is
scheduled to finish at 20:37:07. It runs independently under the user launchd job
`com.openrelife.memory-monitor.20261016-123658`, monitor PID 55934. Source:
`tests/manual/monitor_macos_memory.py`; its seven trend-analysis tests pass.
The live report and raw samples are in
`logs/memory-monitor-20261016-123658/report.md` and `samples.jsonl`.

It samples process-attributed physical footprint and RSS every 60 seconds, tracks
the backend by PID and native start time, records child processes separately, and
saves a native `vmmap` summary hourly or when a new warning appears. Fifteen-minute
medians distinguish brief peaks from sustained elevation; slower growth is checked
after one hour. Restarts and missing observations, including Mac sleep, limit the
automatic conclusion. Warnings and the final assessment are written to the local
report/event files; this does not schedule a future chat notification.

The pre-monitor native map at 12:34 reports 4.4G footprint, including 3.0G virtual
Malloc Large (empty), of which 2.6G is resident and 337.6M swapped/compressed.
The Foundation image-buffer accumulation and large GPU graph storage are absent.
At 12:37, footprint has returned to approximately 2.6G (4.5G process peak).
This distinguishes allocator-retained empty pages from the original retained
native object heap, but also demonstrates why the short initial 1.5G observation
was insufficient to declare long-term stability. No app restart or new memory
mitigation was applied during setup, preserving the ongoing observation.

At the user's request, a separate global RAM monitor was started at 15:10:10,
under launchd job `com.openrelife.system-memory.20261016-123658` (PID 10142).
It samples every five minutes and shares the original 20:37:07 deadline, without
interrupting the OpenReLife collector. Source: `tests/manual/monitor_system_memory.py`.
Five additional unit tests cover page-size conversion, physical versus logical
compressed storage, swap counter rates/resets and grouping helper processes.

Its initial sample reports 32 GiB physical RAM, normal OS pressure, 4.23 GiB
occupied by the compressor and 5.92 GiB swap usage. Summed process footprints are
4.93 GiB for Comet, 3.55 GiB for ChatGPT and 2.05 GiB for the OpenReLife app family.
Shared memory and per-process accounting mean these sums are not an additive
partition of physical RAM. The monitor records new swap writes between samples,
OS warning/critical pressure, and the largest app groups and individual processes.
It reads executable names and metrics, not process arguments or application content.
Results are in `logs/memory-monitor-20261016-123658/system-report.md`,
`system-status.json`, `system-samples.jsonl` and `system-events.jsonl`.

## Follow-up: attribution and corrections in 1.1.2

`heap --forkCorpse --sortBySize --noContent 49403` examined a snapshot of the original
backend. The full class table is in `live-heap.txt`; named-class aggregates are in
`heap-attribution.json` in the evidence directory. It reports 98,035,830 allocations,
totalling 12,967,169,520 malloc bytes (12.08 GiB). In particular:

| Allocation group | Measured amount | Attribution and limit |
| --- | ---: | --- |
| Foundation VM regions | 16.1G in 3,188 regions | Matches the native image-buffer retention reproduced below; VM storage is additional to the malloc class table |
| `CRImageReaderOutput` | 14,646,595 objects, 2.62 GiB | TextRecognition output objects, a direct match to the OCR path |
| Named Vision objects | 0.47 GiB | Rectangle/text observations, recognized text, request specifiers and related objects |
| Named MetalPerformanceShadersGraph objects | 0.86 GiB | Includes 7,127 `MPSGraphExecutable` objects and associated graph/compiler state |
| Named CoreFoundation objects | 0.74 GiB | Strings, arrays, dictionaries and other shared containers; not exclusively attributable to one caller |
| Named Foundation objects | 0.29 GiB | Includes over five million `NSConcreteValue` objects |
| Untyped malloc allocations | 51,198,767 blocks, 6.88 GiB | `non-object`; call-site attribution is unavailable in this historical process |
| Graphics VM regions | 2.7G IOAccelerator graphics plus 1.1G owned unmapped graphics | GPU/driver resources; distinct from the named malloc graph objects |

The rows mix VM regions and subgroups of the malloc heap and are not an exact
additive partition of physical footprint. Neither an object count nor a virtual
region size should be substituted for the physical-footprint accounting.

`leaks --forkCorpse --autoreleasePools --groupByType --noContent 49403` additionally
found 8,579,783 pool entries, 7,897,379 unique entries, and 962.2M of malloc storage
held exclusively by pools including their content blocks. This is not a total for
the Foundation VM buffers or every object also retained elsewhere. The complete
trace finished through thread 175; its verified gzip copy is
`live-autorelease-pools.txt.gz`, with a compact header in
`live-autorelease-summary.txt`. No object contents were requested.

The shipped changes are:

1. `apple_vision_ocr.extract_text_with_vision` now scopes each invocation in a local
   autorelease pool. Native locals live in a helper frame and are dropped before
   the pool drains; returned text and coordinates are Python values. Exceptions
   drain the pool too. `NSData` remains alive through recognition.
2. `nlp.get_model` explicitly uses CPU on macOS. Before this change, the installed
   model selected `mps:0`. On 120 varied synthetic text batches, with a pool per
   call already in place, footprint rose from 1,340.09 to 4,151.17 MiB. Its native
   map included 1.7G Malloc Small and 1.1G IOAccelerator graphics. This independently
   reproduces growth in the GPU embedding path; Vision is not invoked by this test.
   The same workload on CPU ended at 510.45 MiB and fell to 464.97 MiB after worker
   exit, with checkpoints decreasing after warm-up. Other platforms retain automatic
   device selection. Three synthetic CPU/MPS vector comparisons give cosine
   similarities of 0.99999994, 0.99999994 and 1.0, with maximum absolute component
   difference 2.02e-7 (`embedding-equivalence.json`). CPU inference trades GPU
   acceleration for predictable resource use.
3. The macOS app-name, window-title and browser-window helpers now drain local pools
   and convert native string results before returning. With Screen Recording and
   Accessibility permissions confirmed for the diagnostic process, 3,000 metadata
   cycles without a pool rose from 26.30 to 42.99 MiB; an external pool per cycle
   stayed near 26 MiB. The corrected helpers without an external pool stayed near
   27 MiB over 3,000 cycles. This replaces the earlier permission-uncertain result.

Validation includes 85 passing tests (one existing dependency deprecation warning),
pool cleanup on failures, Italian and
English OCR output and coordinate checks, and an isolated memory regression test.
The corrected public OCR function completed 120 calls without an external pool:
63.17 MiB at start, 106.59 after warm-up, 76.28 after 120 calls, and no retained
image-sized Foundation regions. See `fixed-vision.jsonl` and `fixed-vision-vmmap.txt`.

The packaged backend also completed 80 synthetic `/api/ocr-now` requests, each with
a new database stub, image loading, real Vision OCR, CPU embedding generation, and
database persistence. This used a temporary storage directory, not user recordings.
The final package stayed near 407 MiB after warm-up (`final-pipeline.jsonl`).

The build exposed an existing signing issue: Python rewrote bytecode files inside
the signed bundle. The package now excludes cached bytecode and the Electron launcher
sets `PYTHONDONTWRITEBYTECODE=1`. The final build is signed with the same certificate
and designated requirement as 1.1.1. Dependencies are unchanged; only the project's
own version changes in `uv.lock`.

The original app was closed normally; its main/backend/resource-tracker PIDs were
confirmed absent. The verified replacement was installed at
`/Applications/OpenReLife.app`, preserving the previous app at
`/private/tmp/OpenReLife-before-memory-fix-1.1.1.app`. Version 1.1.2 started at
12:10:55, backend PID 45855. Read-only API checks confirm recording is unpaused,
Screen Recording and Accessibility are granted, and Vision remains enabled.
The startup map reports a 1.5G footprint and 1.6G peak. Further live measurements,
including normal OCR requests for existing pending frames, are recorded in
`installed-verification.jsonl`. All 20 normal requests completed successfully.
Observation ran for 190.1 seconds; the last five samples range from 1,533.89 to
1,554.88 MiB, ending at 1,553.89 MiB. The 12:15:40 native map reports 1.5G footprint
and 2.0G peak. The code signature still verifies after the real workload, both
permissions remain granted, and background Vision OCR completed a five-frame batch
in eight seconds. A short post-restart run demonstrates operation and immediate
relief; multi-day stability remains a longer-term validation.

A second native heap capture at 12:14:42, after 20 successful real pending-frame
OCR requests and a completed background batch, reports 511,617 malloc allocations
and 679,683,968 allocated bytes (648 MiB), down from 98,035,830 allocations and
12.08 GiB. `CRImageReaderOutput` falls from 14,646,595 objects to 206 (39,552 bytes).
There are no `MPSGraphExecutable` entries in the new class table; autorelease-pool
content blocks fall from 17,004 to 21. The original model/runtime/index allocations
are still present, including one approximately 468 MiB malloc block, so the reduced
footprint is not explained by an empty backend. See `installed-heap.txt`.

## Live evidence

- Backend PID 49403, parent OpenReLife PID 49349, launched 2026-10-12 09:58:16.
  The installed application runs Python 3.11 and PyObjC 10.3 from its user-data venv.
- A new `vmmap -summary 49403` capture at 11:52:09 reports a 32.6G physical
  footprint and a 38.9G peak. Foundation occupies 16.1G across 3,188 regions;
  Malloc Small occupies 10.6G. The malloc table reports approximately 98 million
  allocations, 12.1G allocated, and 2% fragmentation. This is inconsistent with
  an explanation based solely on unused virtual address space or fragmentation.
- Earlier independently collected samples, personally reviewed, range from
  33.558 to 33.925 to 32.468 GiB between 11:48:30 and 11:51:30. They establish a
  large retained footprint, not continuous monotonic growth during that interval.
- `vmmap`'s SWAPPED category must not be interpreted as the process's disk swap:
  it includes compressed memory. The earlier system samples report 17.88 GiB of
  used swap and no additional swap-out over three minutes. A low RSS does not
  negate the much larger process footprint.
- The process snapshot contains 157 zsh processes (including the measurement
  shell), 78 gitstatus processes, and zero zombies. The terminal shell tree leads
  to PID 30509, `/Applications/Muxy.app/Contents/MacOS/muxy-server`; it is separate
  from OpenReLife's tree. Those counts do not explain the backend's Foundation heap.
- Installed `apple_vision_ocr.py`, `screenshot.py`, `utils.py`, and `app.py` are
  byte-identical to the worktree at revision `b6e24f1`. SHA-256 values are preserved
  in the evidence manifest. The active settings enable Apple Vision, with a
  five-second capture interval and a 120-second OCR cooldown.

## Reproduced cause

`openrelife/apple_vision_ocr.py:132–135` converts the RGB array to bytes and calls
`Foundation.NSData.dataWithBytes_length_`. This copies the pixels into a native
object. `extract_text_with_vision` (lines 195–230) has no local autorelease pool.
Dropping the Python function locals or running `gc.collect()` does not ensure
release of the native autoreleased storage.

`app.py:3301–3320` calls `ocr_one_frame` inline for `/api/ocr-now/<timestamp>`.
`screenshot.py:441–466` dispatches to Vision and then computes an embedding in that
same process. Waitress uses 16 persistent request threads (`app.py:4727–4731`).
This path therefore bypasses the subprocess lifetime used by background OCR
(`screenshot.py:611–625`). Background subprocess exit bounds its own accumulated
memory; it does not reclaim objects created by on-demand OCR in the parent.

The manual probe uses one Python worker thread, a synthetic repository image
resized to 1920 × 1080 RGB, and the application's installed Python/PyObjC
environment. It calls the existing unmodified conversion/OCR functions. Every
measurement runs Python garbage collection first. Separate process invocations
compare the native pool policies. No app requests or user recordings are used.

| Experiment | Physical footprint, MiB | Native evidence |
| --- | --- | --- |
| 60 Vision calls, no local pool | 64.63 → 188.27 → 271.08 → 360.72 → 451.89 | 356.2 MiB Foundation, 60 regions; still 443.55 MiB after the worker exits |
| 60 Vision calls, pool per call | 63.16 → 105.09 → 86.86 → 78.81 → 78.74 | No cumulative image-sized increase; expected text and word output checked |
| 60 conversions only, one outer pool | 64.42 → 427.47 before drain → 70.99 after drain | 356.2 MiB Foundation, 60 regions before drain |

Each RGB buffer is 6,220,800 bytes (5.93 MiB). Sixty retained buffers account for
approximately 356 MiB, matching the measured Foundation regions. The conversion-only
experiment does not perform recognition: its drop after draining the pool isolates
the image-buffer retention from Vision model loading and OCR result processing.
An earlier independent Vision run likewise fell from 296.33 to 118.13 MiB after
draining one outer pool around 30 calls. Native caches and system memory reclamation
explain why absolute footprint values differ between runs; the image-sized growth
and release are the relevant observations.

## Scope and remaining uncertainty

- The defect and its release mechanism are experimentally confirmed. The matching
  Foundation allocation category and the inline request path make it a strong
  explanation for a substantial part of the live footprint. The live process has
  no allocation-history instrumentation, so exact historical attribution of all
  16.1G Foundation and 10.6G Malloc Small is not established.
- Repeated window/API metadata calls also lack explicit pool management. A
  1,000-iteration probe rose only from 25.81 to 34.25 MiB and did not drop when its
  outer pool drained. That probe runs with a different OS permission identity;
  it cannot exonerate or quantify the app's Accessibility/window enumeration path.
  It does not demonstrate a metadata leak comparable to the reproduced image leak.
- Existing capture logs show background Vision subprocesses completing and exiting.
  A backlog of hundreds of timestamps exists, but the queue holds timestamps,
  not image buffers. A separate performance issue is the per-capture use of
  `system_profiler` and `screencapture`; no evidence here attributes the retained
  Foundation buffers to those short-lived commands.
- The initial investigation added only the diagnostic tool and this report. The
  subsequent corrections, installation and normal OCR verification are recorded
  in the follow-up section above. User settings and recordings were preserved.

## Correction rationale

The implemented local `objc.autorelease_pool()` covers each complete invocation of
the native Vision path, before `_np_image_to_cgimage` and through conversion of
observations into Python text/coordinate values. It retains `ns_data` through
`performRequests_error_` and does not add manual CoreFoundation releases. The buffer-only
drain experiment and pool-per-call OCR comparison support this choice for both
callers, including exceptions. A single pool around an infinite
worker loop would still permit accumulation until that loop ends.

The follow-up repeated the probe against the modified public function without an
external per-call pool, ran correctness/fallback tests, and exercised on-demand
requests using packaged code. Metadata behavior was separately checked with mocked
native values and measured with actual OS permissions. A multi-day live run is
still needed to claim the entire historical footprint problem is eliminated.

## Reproduction and evidence

Tool: `tests/manual/probe_macos_memory.py`. Run with the installed venv's Python or
another environment matching Python 3.11.11 and PyObjC 10.3 on macOS 27.0.1:

```sh
python tests/manual/probe_macos_memory.py vision --pool none --count 60
python tests/manual/probe_macos_memory.py vision --pool call --count 60
python tests/manual/probe_macos_memory.py buffer --pool outer --count 60 --vmmap /tmp/buffer-vmmap.txt
```

macOS may require running the probe outside a restrictive sandbox for Vision or
`vmmap` access. A failed Vision call is a failed experiment, not a zero-growth result.

Local evidence bundle: `/private/tmp/openrelife-memory-investigation-20261016/`.
It contains `live-vmmap.txt`, `prior-measurements.tsv`, `prior-diagnosis.json`,
`vision-none.jsonl`, `vision-call.jsonl`, `vision-outer.jsonl`, `buffer-outer.jsonl`,
`buffer-vmmap.txt`, `vision-vmmap.txt`, `metadata-outer.jsonl`, the probe, numbered
source copies, and `source-manifest.json`. The earlier full process/call-stack
captures remain in `/private/tmp/swap-diagnosis-20261016/`; the new process snapshot
is `/private/tmp/openrelife-processes.txt`. Temporary evidence is local and may be
removed by OS cleanup; the report and reproduction tool are in the worktree.

## Technical references verified on 2026-10-16

- Apple, [Advanced Memory Management Programming Guide — Using Autorelease Pool
  Blocks](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/MemoryMgmt/Articles/mmAutoreleasePools.html),
  archive updated 2012-07-17: “Use Local Autorelease Pool Blocks to Reduce Peak
  Memory Footprint” and “Autorelease Pool Blocks and Threads.” These sections explain
  periodic draining and thread-local pools. HTML has no fixed physical pages.
- PyObjC, [An introduction to PyObjC — Working with
  threads](https://pyobjc.readthedocs.io/en/latest/core/intro.html#working-with-threads),
  current online documentation retrieved on the verification date: explicit pools
  on additional threads and periodic pools in long-running loops. This is not
  presented as version-pinned 10.3 documentation; installed 10.3 behavior was tested.
- PyObjC, [objc API — Memory
  management](https://pyobjc.readthedocs.io/en/latest/api/module-objc.html#memory-management),
  current online documentation: `objc.autorelease_pool`. HTML has no fixed pages.
  The installed PyObjC 10.3 implementation was also read directly in
  `venv/lib/python3.11/site-packages/objc/__init__.py:71–86`: context entry creates
  `NSAutoreleasePool`, context exit deletes the pool proxy.

These are technical platform documents, not regulatory requirements. The proposed
correction is an engineering recommendation supported by the measured experiments.
