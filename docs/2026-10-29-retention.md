# Automatic retention in 1.1.4

The previous settings screen persisted `retention_days` and promised daily
deletion, but no worker applied that setting. The installed store was configured
to keep everything until the user selected 90 days on 2026-10-29. The measured
archive contained approximately 138 GB of screenshots dating back to December
2025; this is historical storage, separate from cumulative ANE compiler writes.

## Behavior

`openrelife/retention.py` starts a single background worker after database
initialization. It applies the saved policy immediately, once every 24 hours
while the application is running, and when either settings endpoint saves a
retention change. Failed runs retry after five minutes. An application that was
closed runs cleanup on its next launch.

Supported policies match the settings UI: keep forever (`-1`), 7, 30, 90, or 365
days. Missing settings default to keep forever. Invalid/corrupt settings stop
cleanup; they never imply an aggressive fallback. The worker rechecks policy
between batches and during its orphan sweep, and stops if the policy changes.

Expiration is strictly earlier than the run's cutoff (current time minus the
selected number of 24-hour days). A timestamp exactly on the cutoff is kept.
Both current epoch-microsecond timestamps and the original epoch-second names
are supported. File modification times are not used to determine expiration.

Database rows are deleted in transactions of at most 250 records, including
their full-text search entries through the existing SQLite triggers. Search
caches and the resident embedding index are notified of committed deletions.
Corresponding screenshot files are then unlinked. If interrupted between these
steps, the next orphan sweep still finds expired files by their names. Missing
images do not block row deletion, and expired files without database rows are
also removed. Unrecognized filenames, directories, and symbolic links are kept.

Database errors propagate and prevent the run from proceeding to its file-only
sweep. File removal errors are counted, logged, and retried later. Retention
does not invoke `VACUUM`: deleted SQLite pages are reusable, while the physical
database file may initially remain the same size. It does not remove system
caches or alter recent screenshots.

The settings endpoints validate retention values and atomically replace the
settings file before waking cleanup. `GET /api/settings/retention` includes a
`cleanup` status object. `retention-status.json` records the cutoff, counts,
errors and completion state; progress is saved every 5,000 database rows.

## Validation

`tests/test_retention.py` exercises a real disposable SQLite database and FTS
index: expiration boundaries, legacy timestamps, missing/orphan files, search
index consistency, retry after failed unlink, rollback after a database error,
policy changes during a run, invalid/missing settings, daily scheduling, and
both settings endpoints. The complete suite passed **129 tests** on this Mac
(one existing dependency deprecation warning).

Local verification artifacts are in `logs/retention-fix-20261029/`, including
the test/build logs and the hashes of sources packaged in the signed 1.1.4 app.
The pre-cleanup inventory records filenames, sizes and database identifiers,
allowing the completed run to be checked against its actual cutoff. These
artifacts remain local and ignored by Git.

## Search-index coverage correction

The live post-cleanup checks also found 26,358 nonempty rows absent from the
full-text index's document metadata. The base rows and screenshot files were
present. Their absence from the index cannot be dated from this inspection.

The previous backfill check used `COUNT(*)` and rowid lookup on an external-content
FTS table without `MATCH`. Those queries read the underlying content table even
when the index is empty; they therefore cannot measure index coverage. See the
official [SQLite FTS5 documentation, section 4.4.4](https://www.sqlite.org/fts5.html#external_content_table_pitfalls),
checked on 2026-10-29 (HTML has no fixed page numbers).

Version 1.1.4 checks `entries_fts_docsize` instead, requires all non-NULL content
rows to be represented, and rebuilds missing entries in one writer transaction.
Empty processed rows are included, matching the existing non-NULL deletion
triggers. Pending NULL-text stubs remain intentionally unindexed. Added tests
reproduce both an empty index and a partially missing index that the previous
coverage check incorrectly accepted.

For a full external-content checksum, NULL rows may be added to the index inside
a temporary transaction, checked, and rolled back; otherwise the strict check
also flags those intentionally omitted rows. This distinguishes expected NULL
omissions from genuinely missing searchable text. The final live verification
records both internal integrity and this adjusted external-content checksum.

## Verified local result on 2026-10-29

The initial automatic 90-day run completed in approximately 132 seconds with
zero file errors: **207,902 expired images (105,404,628,230 bytes)** and 207,761
database rows were removed. Later application restarts removed additional
images as the moving 90-day cutoff advanced. The original run is recorded in
`logs/retention-fix-20261029/cleanup-result.json`.

A separate one-time offline SQLite compaction reduced the database from
12,332,634,112 to 3,389,886,464 bytes. This maintenance operation is distinct
from the scheduled worker's normal deletion and page reuse.

The final installed app reports 1.1.4 and retains the user's settings unchanged,
including 90 days. The final comparison with the pre-cleanup inventory verified
all 62,868 original image files and 62,866 original database identifiers still
inside the latest cutoff. No expired image files or database rows remained.
File sizes, modification timestamps and inode identities of those retained
images were unchanged. Seven additional recent captures account for the higher
final totals of 62,875 files and 62,873 database rows.

SQLite `quick_check`, full-text internal integrity, and the external-content
checksum with temporary NULL rows all passed. No non-NULL content rows were
missing from the rebuilt index, and no orphan index rows remained. The
temporary NULL-index additions were rolled back. See
`logs/retention-fix-20261029/final-verification.json`.

At that snapshot, the disk had 180,186,783,744 bytes available (about 168 GiB).
Global free-space changes include activity outside OpenReLife; the deletion
counts and individual database sizes above measure this operation directly.
