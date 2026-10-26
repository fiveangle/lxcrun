# lxcrun

`lxcrun` runs commands and scripts in one or more Proxmox LXC containers using
`pct`. Run it on the Proxmox VE host as root or with equivalent `pct` privileges.

Run a local script by its path, or choose an execution mode explicitly:

```text
lxcrun [options] [VMID ...] --remote COMMAND [ARG ...]
lxcrun [options] [VMID ...] --local SCRIPT [ARG ...]
lxcrun [options] [VMID ...] ./SCRIPT [ARG ...]
lxcrun --delete-snapshots[=NAME] [selection]
```

`--remote` executes a command or script already inside each selected container.
`--local` copies a readable script from the Proxmox host into each selected
container, then executes that copy. A path starting with `/` or `./` is shorthand
for local mode, so `--local` is optional for these paths. A bare filename, such
as `maintenance.sh`, requires `--local maintenance.sh`.

Put all lxcrun options, container IDs, and exclusions **before** the local script
path or the `--remote`/`--local` switch. Everything after the command/script
path belongs to that command/script. No additional `--`
separator is needed. Even `-y`, `--help`, negative numbers, and lxcrun option
names are passed through literally.

Examples:

```bash
# Run a command on all local containers
lxcrun --all --remote dpkg --configure locales

# Run on running containers except VMIDs 100 and 101
lxcrun --running -100 -101 --remote dpkg --configure locales

# Execute a script already INSIDE those containers, passing -y
lxcrun --running -100 -101 --remote /root/maintenance.sh -y

# Copy a script FROM THIS HOST into those containers, then execute it with -y
lxcrun --running -100 -101 --local ./maintenance.sh -y

# Equivalent shorthand for the same host-side script
lxcrun --running -100 -101 ./maintenance.sh -y

# Copy a host-side script to explicit containers and pass its arguments
lxcrun 100 101 --local ./configure.sh --environment production

# Choose containers from an interactive list
lxcrun --local ./audit.sh

# Start stopped containers for a local script, then shut them down again
lxcrun --stopped --restore-state --local ./upgrade.sh

# Snapshot selected containers before running a command
lxcrun --snapshot 100 101 --remote dpkg --configure locales

# Keep a copied script in a custom directory
lxcrun --keep --remote-dir /root/scripts 100 --local ./audit.sh
```

With `--remote`, the executable is resolved inside each container. Files with
the same name on the Proxmox host are never copied or checked. An absolute
path without `--remote` always refers to a host-side script. `--keep` and
`--remote-dir` apply only to local scripts (including shorthand paths) and are
rejected with `--remote`.

No shell is invoked automatically. For shell expressions, explicitly invoke a
shell and quote the expression:

```bash
lxcrun --running --remote sh -c 'command1 && command2'
```

## Container selection

Use explicit VMIDs, a group, or a group plus explicit additions without prompting:

- `--all`: every local container.
- `--running`: containers running when the initial list is loaded.
- `--stopped`: containers stopped when the initial list is loaded.

These group switches are mutually exclusive. Positive VMIDs expand a group
regardless of container state; negative VMIDs exclude containers from the
combined selection. All selection arguments precede the local script path
or `--remote`/`--local`.
Normal execution checks still apply if a container changes state afterward.

For example, include stopped VMID 103 alongside all currently running containers:

```bash
lxcrun --running 103 ./maintenance.sh -y
```

VMID 103 is started for execution unless `--no-start` is specified, in which
case it is skipped. Add `--restore-state` to shut it down again afterward.
Group members run first, followed by explicit additions in their supplied order.
Each container is processed only once, even if selected more than once.

To exclude containers, prefix each VMID with one dash. Exclusions take
precedence over both group membership and explicit additions, regardless of
argument order:

```bash
lxcrun --running -101 -103 --remote dpkg --configure locales
lxcrun --running 103 -101 --restore-state ./maintenance.sh -y
lxcrun --all -101 --snapshot --local ./upgrade.sh
lxcrun --stopped -101 --restore-state --local ./upgrade.sh
lxcrun --delete-snapshots=before-upgrade --running -101 -103
```

Exclusions require `--all`, `--running`, or `--stopped`. Duplicate exclusions and
IDs outside the combined selection have no effect. Leading zeroes are ignored
in VMIDs (`-00101` excludes VMID 101, even if added as `00101`). Excluded
containers receive no per-container operations, including configuration reads, snapshots, starting,
or deletion. They do not count as skipped or failed. No matching containers
is a successful no-op.

Omit VMIDs and group switches for interactive selection. The list shows VMID,
Proxmox hostname, and state. Enter the numbered list positions (not VMIDs),
separated by spaces or commas, or enter `all`. Enter an empty selection to
cancel. Interactive selection requires a terminal.

## Execution and snapshots

Stopped containers are started and left running by default. Use `--no-start`
to skip them, or `--restore-state` to shut down only containers started by
this invocation. `--stopped` selects targets; it does not preserve their stopped
state unless `--restore-state` is also used. Container templates are skipped.
Runs are sequential, and a failure does not prevent the remaining containers
from being processed.

For local scripts, copies are placed in `/tmp` by default. Use `--remote-dir DIR`
to choose another absolute directory. Copies are removed after execution unless
`--keep` is specified.

Use `-s` or `--snapshot` to snapshot each selected container before starting it
or copying a script. Automatic names use local time: `lxcrun-yyyyddmm-hhmmss`
(year, day, month). All containers in an invocation use the same name.
A same-second name collision causes snapshot creation to fail and skips
execution in that container. Other selected containers continue to be processed.

For custom names, use `--snapshot=NAME` or `-s=NAME`; `=` is required:

```bash
lxcrun --all --snapshot=before-upgrade --local ./upgrade.sh
```

Skipped containers are not snapshotted. Snapshots are retained after execution.

## Snapshot cleanup

Run cleanup without an execution mode or command/script:

```bash
lxcrun --delete-snapshots 100 101   # Specific containers
lxcrun --delete-snapshots          # Interactive selection
lxcrun --delete-snapshots --all    # All containers on this host
```

Without a name, cleanup deletes only names matching the complete generated
format `lxcrun-yyyyddmm-hhmmss`: eight date digits and six time digits.
A manually created snapshot with an exactly matching name is also eligible.
Other names are preserved.

To remove one particular snapshot across containers while keeping newer ones,
supply its exact name with `=`:

```bash
lxcrun --delete-snapshots=lxcrun-20261109-171311 100 101
lxcrun --delete-snapshots=before-upgrade       # Interactive selection
lxcrun --delete-snapshots=before-upgrade --all # This name on every local container
```

Named cleanup supports custom and generated names. Matching is literal, with
no wildcards or prefix matching; other snapshots are preserved. A container
without a matching name is a no-op. Group selection switches control containers
only, not which snapshot names match.

Cleanup works with running or stopped containers without starting them.
Locked containers are skipped, with locks checked before each deletion. No locks
are removed and deletion is never forced. Processing continues after failures.
The final summary reports snapshots deleted, deletion failures, and containers
completed, locked/skipped, or failed. Cleanup requires `pvesh` and Perl's
`JSON::PP` module. Execution modes and options cannot be combined with cleanup.

## Reporting

Each container starts with a `[VMID name]` banner; lxcrun status messages use
the same prefix. Guest stdout/stderr remain unchanged. The final issue list
identifies warnings, failures, and skips by VMID/name, including snapshot and
copied-script cleanup issues. Names fall back to the container list, then
`unknown` when configuration cannot be read.

Lifecycle events/results are also sent to syslog with `logger`, tag `lxcrun`,
and `run=PID` to distinguish invocations. View them with `journalctl -t lxcrun`.
Syslog destinations and retention follow the host configuration. Logger failure
warns once and execution continues with terminal reporting.

Guest output and command arguments are not sent to syslog. Guest text saying
"warning" is not classified automatically; execution failures use exit status.
A copied-script cleanup warning retains a successful exit status if execution
itself succeeded.

Exit status is `0` for success (including no matches, help, or an empty
interactive selection), `1` for failed or skipped containers, and `2` for
usage or setup errors.

## Installation and help

Install somewhere on your `PATH`:

```bash
install -m 0755 lxcrun /usr/local/sbin/lxcrun
lxcrun --help
```

`-h` and `--help` show the full guide with examples. No arguments show a compact
option overview. Help flags after `--remote` or after a `--local` script path
are passed to that executable instead of showing lxcrun help. The same applies
to help flags after a shorthand local script path.

## Tests

Run the mocked integration tests without modifying any live containers:

```bash
bash -n lxcrun
python3 -m unittest discover -s tests -v
```

---

lxcrun - by David Johnson (@fiveangle)
