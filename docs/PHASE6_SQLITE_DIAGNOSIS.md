# Phase 6 SQLite diagnosis — separate from authorization

Date: 2026-09-15. No Hermes or dependency changes.

## Finding

The probe error is SQLite extended error **1032 / SQLITE_READONLY_DBMOVED**,
not a simple missing write permission. A minimal Python sqlite3 program with
no Hermes imports reproduces it on this project's volume.

The mounted volume reports `exfat, local, nodev, nosuid, noowners, noatime,
fskit`. The project's Python links SQLite 3.45.3.

On a newly created database, the observed inode changed between connection
creation and the first committed schema write:

```text
inode at connect:       18446744073709526680
inode after CREATE:     234882
next CREATE INDEX:     SQLITE_READONLY_DBMOVED (1032)
```

Precreating the file did not prevent it: a second case changed from
`18446744073709526678` to `234884` and failed identically. These are observations
from disposable files, not stable identifiers to assert in tests.

This supports an exFAT/FSKit file-identity transition causing SQLite to believe
the database moved. The exact OS/SQLite implementation responsible has not been
established; no comparison on another volume was performed because the user's
instruction limits file writes to the project.

## Controls performed

All files were created in private temporary directories under project
`artifacts/` and only those temporary directories were removed. No scheduler,
provider, MCP, network, or reminder script ran.

| Minimal case | Result |
|---|---|
| DELETE journal, create table then create index | Fails with DBMOVED |
| Same with caught duplicate-column ALTER between them | Same failure; ALTER is not necessary |
| Close/reopen connection after initial schema creation | Index creation passes |
| MEMORY journal, table/index/insert/integrity check | Passes; diagnostic control only |
| Precreate database file before connect | Still fails with inode transition |
| Directory mode and writable check | Temporary directory 0700; file writable |

Hermes' `cron/executions.py:_initialize_schema` creates the table, calls
`add_column_if_missing`, then creates indexes. The reported error occurs at
index creation; the pure-SQL control shows the migration helper is not needed
to trigger it. `cron/occurrences.py:completed_occurrence` catches this error and
returns false, so the earlier probe did not establish durable deduplication.

Hermes choosing DELETE journal mode due to its SQLite-version policy explains
which path was exercised. It does not prove that the WAL-reset issue caused
DBMOVED. Do not conflate the warning and this reproducible error.

## Proposed next step, no fix applied

Validate the same minimal test and real Hermes ledger on an approved APFS
project-local location (or after an explicitly approved project relocation).
This requires a separate filesystem decision if the location is outside the
current project. Retain durable journaling and full synchronization.

Do not switch production to MEMORY/OFF journal, suppress the exception, chmod
unrelated files, upgrade Hermes/SQLite, or apply a reopen-after-each-write
workaround based on this probe. Reopening once passing an index test does not
prove crash durability, concurrency, or restart correctness.

Authorization hook approval and SQLite storage resolution are independent;
both must pass before real Phase 6 execution is enabled.
