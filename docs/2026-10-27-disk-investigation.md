# Disk pressure and repeated Vision OCR workers

Evidence checked on 2026-10-27, Europe/Rome. Local release: 1.1.3.

## What the incident evidence establishes

The macOS diagnostic records **137.44 GB of file-backed memory dirtied** by
`ANECompilerService` from 14:14:23 to 16:51:48. This is a cumulative activity
counter, not a measurement of retained files or physical SSD writes. It cannot
by itself explain the reported 90 GB decrease in available space.

- Original: `/Library/Logs/DiagnosticReports/ANECompilerService_2026-10-27-165201_MPB-2021-Alex.diag`,
  lines 1–31 (interval, requesting processes and write accounting), line 48
  (4,314 MB free). A copy is in `logs/disk-investigation-20261027/ANECompilerService.diag`.
- The report lists 11,044 samples originating from 29 Python process IDs and
  2,037 samples from unidentified processes. All 29 listed Python IDs match
  TCC attribution entries whose responsible application is `com.openrelife.app`.
  See `confirmed-ocr-client-pids.txt`, lines 1–29, and `client-attribution.json`
  in that evidence directory. This links OpenReLife to the heavy compiler
  activity; it does not assign every byte to OpenReLife.
- `capture-before.log`, lines 8558–8964, contains 60 worker starts and 11 timeout
  events inside the diagnostic interval. There are 61 batches overlapping the
  interval if the batch already running at its beginning is included. The
  timeout following the interval is excluded from the count of 11.
- Saved screenshot files whose timestamps fall inside the interval total
  286,098,036 bytes across 751 files (`initial-findings.json`). These surviving
  files cannot explain a 90 GB increase. This is not a history of deleted files.

The code previously let Vision choose its compute device and kept restarting
timed-out batch processes without a persistent failure limit. Repeated model
preparation by short-lived OCR workers is a plausible contributor. The exact
Apple compiler/cache failure and the destinations of all missing bytes remain
unproven: no privileged file-write trace was captured during the incident.

## Why activity continued with OpenReLife closed

After reboot, checks found no OpenReLife application, backend or OCR worker.
Apple's `aned` logs nevertheless showed OCR/ANE requests from PID 1714, whose
executable was `VisualIntelligenceServices.framework/visualintelligenced`.
See `ane-clients-after-reboot.log` and `ane-client-last-five-20261027.log`.
Apple's own service can therefore account for activity while OpenReLife is
closed; this does not establish its share of the lost space.

The root-owned ANE/Core ML caches could not be measured: cache reads returned
permission errors and `sudo -n lsof` required authentication. No system cache
was deleted. APFS Data snapshots were absent at inspection. Swap was zero
after reboot; the 16 GB `/private/var/vm/sleepimage` predates the incident.
The two observed pCloud cache files retained their logical sizes, which does
not exclude writes or changes in allocated blocks.

## Changes in 1.1.3

- Vision requests select and read back a CPU device for each exposed compute
  stage. Unsupported or rejected CPU selection fails rather than silently
  allowing the default device; the dispatcher can use its CPU fallback.
- Batch processes use `spawn`, are serialized against on-demand OCR, and are
  killed and collected on timeout.
- Two consecutive failed workers pause OCR persistently across restarts.
  Resumption requires the explicit settings action. Ordinary settings loads
  and bulk saves do not clear the pause.
- OCR cannot start below 10 GiB free on the storage volume. Screenshot capture
  pauses below 2 GiB. Pending frames are retained; these checks do not reclaim
  space or stop other applications from consuming it. A running batch may
  continue until it finishes or times out.
- Settings display the safety state and offer explicit resumption. The
  on-demand API returns HTTP 503 when safety checks defer recognition.

**Limit:** selecting CPU for the exposed Vision stages does not eliminate all
internal ANE interaction. The first live batch still generated `aned` entries
for worker PID 20209 and small auxiliary models. Its captured 18:02:40–18:03:00
log contains neither `Calling ANECompilerService` nor compiler-process log
entries. This observation is limited to that window and logging coverage.
`existsInCache=0` alone is not proof of new disk compilation. See
`ane-during-first-live-batch.log`, lines 1–244. These changes reduce a suspected
trigger and bound retries; they do not guarantee that an Apple service cannot
grow its caches independently.

## Verification and live trial

- Full automated suite: 110 passed; one existing `defusedxml` deprecation
  warning. After adding a capture-loop low-space regression, all 11 safety
  tests passed. Logs: `full-tests.txt` and `safety-final-tests.txt`.
- Real native device selection was read back as `MLCPUComputeDevice`.
- Thirty packaged on-demand OCR requests and three real spawned batches of
  two synthetic English/Italian frames completed successfully. See
  `packaged-pipeline.jsonl` and `spawned-batches.jsonl`.
- The signed application in `/Applications/OpenReLife.app` reports 1.1.3.
  The previous 1.1.2 application is preserved at
  `/private/tmp/OpenReLife-before-disk-fix-1.1.2.app`. Source hashes used by
  the build are recorded in `build-source-manifest.json`.
- Trial snapshot at 19:23:16: application started at 18:01:36, **11 batches and
  195 frames completed, zero timeouts and zero nonzero worker exits**. Safety
  status reported no pause and zero consecutive failures. See
  `live-trial-capture.log` and `live-trial-ocr-summary.json`.
- The independent disk watcher observed 27.54 GiB free at 17:34:49 and 28.95 GiB
  at 19:23:14, with a 25.71–43.11 GiB range. The range includes periods with the
  application closed and with it running. This is global disk availability,
  not an application-specific write measurement; its fluctuations cannot be
  credited to the fix. See `disk-watch-summary.json` and the original
  `/private/tmp/disk-watch-20261027.jsonl`.

The initial live trial is encouraging but does not reproduce the hours-long
incident or fully account for the earlier missing space.

## Evidence hygiene

The first safety-test run, while OpenReLife was closed, wrote two simulated
120-second timeout messages and a safety-stop message to the real capture log
at 17:46:39. Those messages are test output, not production worker failures.
`capture-before.log` was copied before these tests. `tests/conftest.py` now
sets an isolated temporary storage directory before importing the application;
subsequent suite runs used that isolation.

Raw logs contain local application/window metadata and remain in the ignored
`logs/` directory. They are local evidence, not published artifacts.
