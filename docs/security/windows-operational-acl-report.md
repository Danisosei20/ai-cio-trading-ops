# Windows operational ACL report

Observed (read-only, 2026-11-24):
- Identity: `danizpc\danis akoto`.
- 10 `AI-*` scheduled tasks: principal `Danis Akoto`, Interactive, Limited, Ready.
- Keystore `%LOCALAPPDATA%\ai-cio-trading-ops\authorization_keys.json`: NOT PRESENT.
- Authorization DB / audit log in live checkout: NOT PRESENT at inspection.

Therefore real ACL tests (unrelated-user denial, inheritance, junction
redirection, post-rotation permissions) are **NOT TESTED**. Linux CI is not a
substitute. Code applies DPAPI + atomic writes + best-effort `icacls` but
this is not verified evidence.

Owner actions required before unattended certification:
1. Run `python scripts/auth_bootstrap.py initialize --root <repo> --admin-confirm INITIALIZE-AUTH-STORE`.
2. `icacls` the keystore, DB and audit log; confirm only owner + SYSTEM.
3. Verify an unrelated local user cannot read/modify these artifacts.
4. Record the results in this document with redacted identifiers.
