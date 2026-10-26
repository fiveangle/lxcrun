# Commit guidelines

Use short, descriptive commit subjects with these simple prefixes:

- `feat:` for new features or capabilities.
- `fix:` for bug fixes.
- `refactor:` for code restructuring that preserves existing behavior.
- `doc:` for documentation changes.
- `exp:` for explorations or experiments.

These are guidelines, not strict rules. Choose the prefix that best describes
the main purpose of the commit and keep related changes together. For example,
a bug fix and its accompanying documentation and tests can share one `fix:`
commit; the documentation does not need a separate `doc:` commit. Likewise,
documentation and tests supporting a feature can be included in its `feat:`
commit.

# Branch guidelines

Before working on a change, create a descriptive branch using the same prefix
categories, with a slash instead of a colon: `feat/`, `fix/`, `refactor/`,
`doc/`, or `exp/`. For example, use `fix/vmid-omission` for a VMID-related fix.
Choose the category that reflects the main purpose of the work; related
documentation and tests can remain on that branch.

When work on a branch is complete and validated, ask the user whether it should
be merged into `main` via fast-forward. Do not merge without their approval.
Use `git merge --ff-only` for an approved merge. If a fast-forward is not
possible, explain the situation and ask how to proceed rather than creating a
merge commit or rewriting history automatically.
