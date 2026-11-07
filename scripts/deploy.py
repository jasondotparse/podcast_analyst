#!/usr/bin/env python3
"""Publish the working tree to ECR and restart Compose via SSM, or deploy an existing tag."""

import argparse
import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent
REGION = "us-east-1"


def run(*command: str, capture: bool = False, **kwargs) -> str:
    result = subprocess.run(command, cwd=ROOT, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None, **kwargs)
    return result.stdout.strip() if capture else ""


def aws(*arguments: str):
    return json.loads(run("aws", "--region", REGION, "--no-cli-pager", *arguments,
                          "--output", "json", capture=True))


def release_archive() -> str:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for filename in ("compose.yaml", "config/Caddyfile"):
            bundle.add(ROOT / filename, arcname=filename)
    return base64.b64encode(archive.getvalue()).decode("ascii")


def remote_command(repository: str, tag: str, url: str) -> str:
    registry = repository.split("/")[0]
    environment = f"APP_IMAGE={repository}:{tag}\nAPP_DOMAIN={url.removeprefix('https://')}\n"
    environment64 = base64.b64encode(environment.encode()).decode("ascii")
    return f"""set -eu
# SSM may register before cloud-init has finished installing Docker/Compose.
for attempt in $(seq 1 120); do
    test ! -f /opt/podcast-analyst/.bootstrap-complete || break
    sleep 5
done
test -f /opt/podcast-analyst/.bootstrap-complete
cd /opt/podcast-analyst
# Prevent two local deploy commands from updating the same host simultaneously.
exec 9>.deploy.lock
flock -n 9
# Keep Codex state on the host's EBS filesystem, writable by the app user.
install -d -m 700 -o 10001 -g 10001 /var/lib/podcast-analyst/codex
printf %s {shlex.quote(release_archive())} | base64 --decode | tar -xz
printf %s {shlex.quote(environment64)} | base64 --decode > .env.next
aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {shlex.quote(registry)}
docker compose --env-file .env.next --profile cloud pull --quiet
if ! docker compose --env-file .env.next --profile cloud up -d --no-build --wait --wait-timeout 120; then
    docker compose --env-file .env.next --profile cloud ps
    docker compose --env-file .env.next --profile cloud logs --tail 50
    exit 1
fi
docker compose --env-file .env.next --profile cloud exec -T edge caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
if test -f .env; then cp .env .env.previous; fi
mv .env.next .env
docker compose --profile cloud ps
"""


def wait_for_command(command_id: str, instance_id: str) -> None:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        # list-command-invocations can initially return an empty list.
        invocations = aws("ssm", "list-command-invocations", "--command-id", command_id,
                          "--instance-id", instance_id, "--details")["CommandInvocations"]
        if invocations:
            invocation = invocations[0]
            status = invocation["Status"]
            if status not in ("Pending", "InProgress", "Delayed"):
                for plugin in invocation.get("CommandPlugins", []):
                    print(plugin.get("Output", ""), flush=True)
                if status != "Success":
                    raise RuntimeError(f"SSM deployment {command_id}: {status}")
                return
        time.sleep(5)
    raise RuntimeError(f"Timed out waiting for SSM command {command_id}; inspect it in AWS before retrying")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("beta", "prod"))
    parser.add_argument("image_tag", nargs="?", help="Reuse an ECR tag for rollback; skip building")
    args = parser.parse_args()
    if args.image_tag and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.image_tag):
        parser.error("Invalid Docker image tag")

    stack = aws("cloudformation", "describe-stacks", "--stack-name", f"PodcastAnalyst-{args.stage}")["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"):
        raise RuntimeError(f"Stack is not ready: {stack['StackStatus']}")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    repository, instance_id, url = (outputs[key] for key in ("RepositoryUri", "InstanceId", "AppUrl"))

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        info = aws("ssm", "describe-instance-information", "--filters",
                   json.dumps([{"Key": "InstanceIds", "Values": [instance_id]}]))["InstanceInformationList"]
        if info and info[0]["PingStatus"] == "Online":
            break
        print("Waiting for the EC2 SSM agent...", flush=True)
        time.sleep(5)
    else:
        raise RuntimeError("EC2 is not online in SSM; check instance role and bootstrap logs")

    tag = args.image_tag
    if tag is None:
        commit = run("git", "rev-parse", "--short=12", "HEAD", capture=True)
        tag = f"{commit}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
        if run("git", "status", "--porcelain", capture=True):
            print("Building the current working tree, including uncommitted source changes.", flush=True)
        password = run("aws", "--region", REGION, "ecr", "get-login-password", capture=True)
        run("docker", "login", "--username", "AWS", "--password-stdin", repository.split("/")[0], input=password)
        print(f"Building {repository}:{tag} for linux/amd64", flush=True)
        run("docker", "buildx", "build", "--platform", "linux/amd64", "--provenance=false",
            "--tag", f"{repository}:{tag}", "--push", ".")
    else:
        aws("ecr", "describe-images", "--repository-name", repository.split("/", 1)[1],
            "--image-ids", f"imageTag={tag}")

    print(f"Deploying {tag} to {args.stage}", flush=True)
    result = aws("ssm", "send-command", "--instance-ids", instance_id,
                 "--document-name", "AWS-RunShellScript", "--timeout-seconds", "600",
                 "--comment", f"podcast-analyst {tag}", "--parameters",
                 json.dumps({"commands": [remote_command(repository, tag, url)], "executionTimeout": ["840"]}))
    command_id = result["Command"]["CommandId"]
    print(f"SSM command: {command_id}", flush=True)
    wait_for_command(command_id, instance_id)

    # Caddy may still be obtaining its first certificate after Compose starts.
    for attempt in range(24):
        try:
            with urlopen(url, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(f"Unexpected HTTP status: {response.status}")
            print(f"Live: {url}\nRelease: {tag}", flush=True)
            return
        except (URLError, TimeoutError, OSError):
            if attempt == 23:
                raise RuntimeError(f"Containers started, but HTTPS verification failed at {url}. Check DNS and Caddy logs.")
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
