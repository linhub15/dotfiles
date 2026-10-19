# MEGA status for Strata

A read-only external provider using stock MEGAcmd. No custom MEGAcmd build or
MEGAsync GUI is required. Python 3's standard library is sufficient on Linux.

## Status and icons

Configured sync roots retain MEGAcmd's aggregate engine status. Files and
subfolders show the setup's chosen metadata-based inference:

| Badge | Meaning |
| --- | --- |
| Green check | Synced: file size/timestamp match, or the complete indexed folder subtree matches |
| Amber clock | Local changes: a filesystem event invalidated the saved status |
| Amber not-equal | Metadata differs from the cached cloud copy |
| Grey minus | Missing in the cached cloud listing |
| Grey question mark | Unknown, unchecked, unavailable or unsupported |

Files marked Synced match byte size and modification time to the second; no
content hash comparison is performed. Folder checks include all indexed local
and cloud names/types and descendant file comparisons. Empty folders can match.
Differences or missing entries prevent a folder checkmark. Excluded/local-only
hidden files can cause differences: this does not reproduce MEGA's ignore rules.
These are cached metadata inferences, not the SDK's per-file transfer states.
The provider never changes sync configuration or cloud/file contents.

**Check MEGA status** in the context menu shows counts and the verification basis.
Labels are currently English. Visual presentation is not covered by automated
GUI tests; protocol behavior and watcher updates are tested independently.

## Index once, then watch changes

When Strata first starts the provider, it discovers configured sync roots and
indexes every reachable local file/folder below them in a background worker.
Matching statuses are saved, rather than inferred solely from a root's status.
It adds a Linux **inotify directory watch before scanning each directory**.

Normal browsing uses the persisted status index. It does not recursively scan a
folder or list its cloud children every time it is opened. File/directory identity
metadata is checked cheaply; Linux events provide the recursive dirty tracking.

On a write, attribute change, create, delete or rename:

1. Mark the affected path/subtree and ancestors dirty in SQLite.
2. Send Strata a scoped invalidation event, so its badges can update immediately.
3. Coalesce repeated events into one parent-directory check after one second.
4. Refresh that directory's cloud metadata and recompute its direct entries and
   ancestor aggregates. Clean sibling subtrees reuse their indexed results.
5. If changed items have not caught up, retry after 2, 8, 30 and 120 seconds.
   After that, retain the comparison result until another event or the audit.

New directories are watched and indexed recursively. Ordinary directory deletion
refreshes its parent. Directory moves that invalidate watch paths, lost inotify
history/overflow, or an unexpected removed watch invalidate the affected session
and trigger a fresh root index. A failed watcher never authorizes cached green
folder badges; folders fall back to Unknown and background checks.

The watcher only runs while the provider process is alive in Strata. On startup,
a local-tree scan catches changes made while Strata was closed. Unchanged paths
reuse cloud snapshots from SQLite; changed directories refresh their metadata.
The startup scan is needed because no filesystem events were observed while the
provider was stopped, including created and deleted entries. Matching files retain their saved status; folder aggregates
become available after the startup index completes.

## Persistent cache and request policy

```text
$XDG_CACHE_HOME/strata-mega/metadata.sqlite3
# Default: ~/.cache/strata-mega/metadata.sqlite3
```

The private SQLite database contains:

- `snapshots`: directory handles, immediate cloud child metadata and fetch times.
- `statuses`: local paths/parents, kinds, identity fingerprints, comparison states,
  dirty flags and check times.
- `directories`: cloud-directory mapping and the directory's own comparison basis.
- `audits`: last audit time for each sync-root mapping.

No file contents, credentials, account session tokens or passwords are stored.
Directory permissions are 0700 and the database is 0600. SQLite transactions and
short busy timeouts protect concurrent readers/writers. If disk caching cannot
be opened, the provider uses an in-memory SQLite database.

Successful cloud snapshots and status records are valid for **24 hours**, unless
invalidated sooner by changes. Failed listing snapshots have a **15-second**
negative-cache lifetime. Ordinary file events refresh only the affected directory;
cloud metadata is addressed by node handle, avoiding wildcard interpretation of
filenames. Two paired CLI renderings cross-check directory names and handles
before a listing can be accepted. A changing/ambiguous listing becomes Unknown.

A **six-hour background audit** refreshes the complete configured tree as a
backstop for cloud-only changes without local filesystem events. Its deadline is
saved in SQLite, so restarting Strata does not postpone it indefinitely. Cached
metadata cannot immediately reveal a cloud-only change that has not affected the
local filesystem; inotify is a local event source, not a MEGA server event feed.

The root status/configuration command is cached for **30 seconds**. Browsing
unchanged items does not produce additional directory-listing calls. `mega-ls`
reads MEGAcmd's existing cloud-node tree; the provider itself does not request web
scans/downloads. MEGAcmd independently maintains network connectivity and syncs.

One worker handles initial indexing, changed directories, bounded retries and
audits. Work is coalesced per directory, with at most 2,048 scheduled tasks.
There are no per-file remote lookups, shell commands or content reads. CLI stdout
is capped at 1 MiB while being read, and each client command has a two-second
timeout; overflow/timeout terminates only that spawned client process group. No selected
filenames, CLI output or credentials are printed in diagnostics.

Bounds: 4,096 directory snapshots; 32 MiB total snapshot JSON; 100,000 persisted
status records; SQLite page limit 32,768 (normally 128 MiB, plus a temporary rollback
journal); 8,192 watched directories; 5,000 immediate entries per directory; depth
32. Paths with controls/non-UTF-8 names, symlink aliases and remote URIs are
unsupported. Incomplete/unsupported checks do not produce green folder badges.

The cache is outside dotfiles and needs no Git ignore rule. To clear it, fully
quit Strata, remove `~/.cache/strata-mega/`, then reopen Strata. It rebuilds itself.

## Install and update

The single maintained source is this dotfiles directory:

```sh
python3 ~/github/dotfiles/omarchy/strata/providers/mega/install.py
```

Fully quit **all** Strata windows and reopen it. The October 13 Omarchy package
loads external providers at startup but has no Integrations settings page.
Newer builds also provide **Settings → Integrations → Refresh**.

The installer copies `provider.py`, `mega_metadata.py`, `mega_index.py`,
`mega_watch.py`, `mega_process.py` and PNG assets to `~/.config/strata/providers/mega/`, and generates
`provider.json` with machine-local paths. `$XDG_CONFIG_HOME` overrides that base.
On older builds, an empty `disabled` file in the installed directory disables the
provider after restarting Strata; remove it and restart to enable again.

Omarchy manages Strata and MEGAcmd package updates. On this machine MEGAcmd comes
from the signed `DEB_Arch_Extra` repository and runs at login via
`~/.config/systemd/user/megacmd.service`. The configured pair is `~/notes` ↔ `/notes`.
The adapter source does not update itself; re-run the installer after edits.

```sh
systemctl --user status megacmd.service
mega-cmd
```

For a fresh MEGAcmd installation, enter `login your-email@example.com` in its shell
and enter your password privately. Configure intended two-way folder pairs with
`sync /absolute/local/folder /cloud/folder`. `quit --only-shell` leaves syncing
running; `logout` removes session sync settings.

## Development

```sh
cd ~/github/dotfiles/omarchy/strata/providers/mega
python3 -m unittest discover -s tests -v
```

Tests cover full bootstrap, no-listing/no-scan cached browsing, persisted status
recovery, edits while stopped, dirty ancestor propagation, scoped retries,
rename/deletion, cloud-only audits and their persisted deadline, actual Linux
inotify writes/new directories, overflow, and ordered provider event/reply frames.
Boundary tests prevent indexing outside the configured roots, and expired child
records cannot refresh an ancestor's green status. Subprocess tests exercise live
output limits and timeouts. Other regressions cover metadata parsing, SQLite expiry/bounds/fallback, installer
permissions and PNG assets. Fixtures use temporary local files and fake MEGA
responses; they do not change cloud data or require account credentials.

Legacy MEGAsync socket and simple metadata adapters remain as helpers/regressions;
the installed entrypoint uses `mega_index.IndexedClient`. Artwork regenerates with
`make_icons.py` (python-cairo at build time only).

References:

- https://github.com/lgse/strata/blob/main/docs/file-providers.md
- https://github.com/meganz/MEGAcmd/blob/2.6.0_Linux/src/megacmdexecuter.cpp
- https://github.com/meganz/MEGAcmd/blob/2.6.0_Linux/src/sync_command.cpp
