"""Create the dashboard sign-in credentials (first-time setup).

    python scripts/create_dashboard_password_hash.py

Prompts for a username and a password (hidden, confirmed) and prints the two values to put in the
Modal secret `job-apply-agent-dashboard-secrets`:

    DASHBOARD_USERNAME=<username>
    DASHBOARD_PASSWORD_HASH=<pbkdf2-sha256:...>

The password itself is never printed, written to disk or logged; only a salted PBKDF2 hash is shown.
See README.md ("Dashboard authentication") for the `modal secret create` command, or use
scripts/reset_dashboard_password.py --apply to do it for you.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard_auth  # noqa: E402


def main():
    try:
        user, phash = dashboard_auth.collect_credentials(input, getpass.getpass)
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 1
    print("\nAdd these to the Modal secret job-apply-agent-dashboard-secrets:\n")
    print(f"DASHBOARD_USERNAME={user}")
    print(f"DASHBOARD_PASSWORD_HASH={phash}")
    print("\n(Keep DASHBOARD_ALLOWED_HOSTS in the same secret. Do not commit these values.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
