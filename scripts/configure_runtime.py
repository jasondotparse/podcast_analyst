#!/usr/bin/env python3
"""Upload backend-only runtime secrets to encrypted SSM Parameter Store."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
from tempfile import NamedTemporaryFile

from dotenv import dotenv_values, set_key


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("alpha", "beta", "prod"))
    args = parser.parse_args()
    env_file = ROOT / ".env"
    values = dotenv_values(env_file)
    api_key = os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("Set OPENAI_API_KEY in the local .env first.")
    password_key = f"PODCAST_{args.stage.upper()}_PASSWORD"
    password = values.get(password_key) or secrets.token_urlsafe(24)
    if len(password) < 8 or any(char in api_key + password for char in "\r\n"):
        parser.error("Use a single-line API key and a single-line password of at least 8 characters.")
    if not values.get(password_key):
        set_key(env_file, password_key, password)
    env_file.chmod(0o600)
    name = f"/podcast-analyst/{args.stage}/runtime"
    # Alpha-only experiments; see docs/etl_design.md#scheduled-thesis-digests.
    experiments = "PODCAST_ENABLE_SCHEDULED_DIGESTS=1\n" if args.stage == "alpha" else ""
    payload = {
        "Name": name, "Type": "SecureString", "Tier": "Standard", "Overwrite": True,
        "Value": f"OPENAI_API_KEY={api_key}\nPODCAST_APP_PASSWORD={password}\n{experiments}",
    }
    # A temporary 0600 file keeps values out of process arguments and SSM commands.
    with NamedTemporaryFile(mode="w", prefix="podcast-runtime-", suffix=".json") as temporary:
        json.dump(payload, temporary)
        temporary.flush()
        subprocess.run(["aws", "--region", "us-east-1", "--no-cli-pager", "ssm", "put-parameter",
                        "--cli-input-json", f"file://{temporary.name}"],
                       check=True, stdout=subprocess.DEVNULL)
    print(f"Updated {name}. Test-user password is saved as {password_key} in the gitignored .env.")


if __name__ == "__main__":
    main()
