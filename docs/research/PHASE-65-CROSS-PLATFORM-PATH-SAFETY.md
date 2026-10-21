# Phase 65 — Cross-platform path safety

## Decision

Local file-change plans are portable inputs: a plan validated on Linux must not acquire a different target meaning when it is applied on Windows. The standard-library validator now rejects Windows-invalid component characters, alternate-data-stream syntax (colon), trailing spaces/periods, and DOS device names (including numbered COM/LPT families). It also rejects case-folded path collisions and inconsistent directory-component casing within one plan.

These rules are deliberately fail-closed. BOTPYTON does not sanitize or silently rewrite a requested destination, because rewriting can change which file a task modifies. No third-party dependency was added.

## Failure modes covered

- file.py:stream can designate an NTFS alternate data stream rather than the intended ordinary file.
- Windows treats names such as CON, NUL, AUX, PRN, COM1–COM9 and LPT1–LPT9 as devices, including when a suffix is present. Superscript-number variants are covered too.
- Windows does not preserve a final period or space in a path component consistently with POSIX filesystems.
- Case-only collisions such as Settings.py and settings.py, and parent components such as src/a.py and SRC/b.py, can represent a different file tree across platforms.

The rule is applied to every component, not only the final filename. The existing traversal, protected-path, symlink/reparse-point, expected-hash, byte-limit, atomic-publication, transaction-journal, and rollback protections remain in force.

## Industry references

- CPython path documentation explains that Windows reserved paths include colon-based streams, trailing spaces/periods, wildcard/invalid characters and device names: https://github.com/python/cpython/blob/main/Doc/library/os.path.rst
- The pathvalidate project independently separates filename/path validation from sanitization, checks platform-specific invalid characters and reserved device names, and avoids assuming that POSIX and Windows path rules match: https://github.com/thombashi/pathvalidate
- Restic's 0.19.1 release notes document correctness fixes around inaccessible paths, mount/repository overlap, and duplicate directory entries in exclusion handling. These reinforce validating path relationships and refusing ambiguous operations instead of proceeding with changed semantics: https://github.com/restic/restic/releases

BOTPYTON implements the small policy it needs with Python's standard library rather than adding a dependency for these checks. The references are design inputs, not claims of feature parity with those larger tools.

## Verification scope

Regression coverage checks Windows-invalid component syntax/device names, file-name case collisions, mixed-case parent-directory collisions and the allowed case of multiple files that share consistently spelled parent components. CI must pass on Linux and Windows for the exact final commit. Windows-hosted CI does not replace a smoke test on the user's own PC.

## Limits

This is path portability validation, not a full emulation of all filesystem-specific rules or Unicode normalization behaviors. The journal's SHA-256 checksum detects accidental corruption; it does not authenticate against a local attacker who can edit the journal and recompute the checksum.
