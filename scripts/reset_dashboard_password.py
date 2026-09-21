"""Change (or recover) the dashboard password without touching source code.

    python scripts/reset_dashboard_password.py            # prints the secret-update command
    python scripts/reset_dashboard_password.py --apply    # also updates the Modal secret for you

Steps it walks you through:
  1. choose a username + new password (hidden, confirmed)   -> a salted PBKDF2 hash is made
  2. update the Modal secret `job-apply-agent-dashboard-secrets`
  3. redeploy:  modal deploy modal_app.py                    (a new password signs out every old session)
  4. sign in with the new password

Nothing is stored in the repository. The plaintext password is never printed or written anywhere; with
--apply the hash goes to `modal secret create` through a temporary 0600-style file that is deleted
immediately (Modal's own login, from `modal setup`, is used -- no Modal credential is read or kept here).

NOTE: `modal secret create --force` replaces the whole secret, so this always writes all three keys
(DASHBOARD_USERNAME, DASHBOARD_PASSWORD_HASH, DASHBOARD_ALLOWED_HOSTS). Other secrets are untouched.
"""
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard_auth  # noqa: E402

SECRET_NAME = "job-apply-agent-dashboard-secrets"
HOST_PLACEHOLDER = "<workspace>--job-apply-agent-dashboard.modal.run"


def secret_values(user, phash, allowed_host):
    return {"DASHBOARD_USERNAME": user, "DASHBOARD_PASSWORD_HASH": phash, "DASHBOARD_ALLOWED_HOSTS": allowed_host}


def update_command(values):
    """The exact command to run, for people who prefer to do it themselves (hash only; no password)."""
    pairs = " ".join(f"{k}={v}" for k, v in values.items())
    return f"modal secret create {SECRET_NAME} {pairs} --force"


def apply_to_modal(values, runner=subprocess.run):
    """Create/replace the secret via a temp JSON file (keeps the hash out of the process list)."""
    modal = shutil.which("modal")
    if not modal:
        raise RuntimeError("The `modal` CLI was not found. Install/log in (`pip install modal`, `modal setup`) or run the printed command yourself.")
    tmp_dir = tempfile.mkdtemp(prefix="dashboard-secret-")
    try:
        path = Path(tmp_dir) / "secret.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        result = runner([modal, "secret", "create", SECRET_NAME, "--from-json", str(path), "--force"],
                        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        if result.returncode != 0:
            raise RuntimeError(f"`modal secret create` failed (exit {result.returncode}).")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reset the dashboard password.")
    ap.add_argument("--apply", action="store_true", help="update the Modal secret directly (needs the modal CLI, logged in)")
    ap.add_argument("--host", default=os.environ.get("DASHBOARD_ALLOWED_HOSTS", ""),
                    help="the dashboard hostname for DASHBOARD_ALLOWED_HOSTS (no https://)")
    args = ap.parse_args(argv)
    try:
        user, phash = dashboard_auth.collect_credentials(input, getpass.getpass, default_user=os.environ.get("DASHBOARD_USERNAME", ""))
    except ValueError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 1
    host = args.host.strip() or HOST_PLACEHOLDER
    values = secret_values(user, phash, host)

    if args.apply:
        if host == HOST_PLACEHOLDER:
            print("\n--apply needs the real hostname: rerun with --host <workspace>--job-apply-agent-dashboard.modal.run", file=sys.stderr)
            return 1
        try:
            apply_to_modal(values)
        except RuntimeError as e:
            print(f"\n{e}", file=sys.stderr)
            return 1
        print(f"\nUpdated the Modal secret {SECRET_NAME}.")
    else:
        print(f"\nUpdate the Modal secret (this replaces its contents, so all three keys are included):\n\n{update_command(values)}")
        if host == HOST_PLACEHOLDER:
            print("\nReplace the DASHBOARD_ALLOWED_HOSTS value with your dashboard hostname (no https://).")
    print("\nNext: redeploy so the new password takes effect (all old sessions are signed out):\n\n    modal deploy modal_app.py\n")
    print("Then open the dashboard and sign in with the new password. Do not commit these values.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
