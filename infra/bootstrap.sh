set -euxo pipefail

dnf install -y docker
systemctl enable --now docker amazon-ssm-agent

# Install a pinned Compose plugin; Amazon Linux does not package it with Docker.
install -d /usr/local/lib/docker/cli-plugins
curl --fail --location --retry 5 \
  https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64 \
  --output /usr/local/lib/docker/cli-plugins/docker-compose
echo '7af95166a730b87e172d4fc9aefea8725d3c6c7327d59149267b452114ddb7d4  /usr/local/lib/docker/cli-plugins/docker-compose' | sha256sum --check
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
aws --version
install -d -m 755 /opt/podcast-analyst
touch /opt/podcast-analyst/.bootstrap-complete
