#!/usr/bin/env bash
# Turns a fresh Debian or Ubuntu machine into the one that runs live (README, Hosting).
# Run it once, as root, on the server and nowhere else:
#
#   ssh root@<server> 'bash -s' < infra/scripts/server-setup.sh
#
# It is idempotent: running it again changes nothing it has already done. It installs no
# secret and asks for none — the OpenRouter key, the mailbox password and the tunnel token
# go into .env.live by hand afterwards, and the script prints the steps that are left.
#
# What it sets up:
#   a jap user that owns the checkout and may talk to Docker
#   Docker CE with the compose plugin
#   a firewall that allows SSH and nothing else, which is all the stack needs: the site is
#     served through the Cloudflare Tunnel, an outbound connection, and every container port
#     binds to 127.0.0.1
#   unattended security updates
#   2 GB of swap, so a Chromium that spikes during a submit is slowed down rather than
#     killed — an OOM between pressing Submit and reading the answer leaves an application
#     JAP cannot tell the outcome of
set -euo pipefail

REPO=${JAP_REPO:-https://github.com/jainAdijain/public-jap.git}
DIR=${JAP_SERVER_DIR:-/srv/public-jap}
USER_NAME=jap

[ "$(id -u)" -eq 0 ] || { echo "Run as root on the server." >&2; exit 1; }
[ "$(uname)" = Linux ] || { echo "This is for the Linux server, not a Mac." >&2; exit 1; }

say() { printf '\n== %s\n' "$1"; }

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# age: `make restore KEY=...` decrypts an encrypted dump on the machine running it.
apt-get install -y -qq age ca-certificates curl git ufw unattended-upgrades

say "Docker"
if ! command -v docker > /dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc 2> /dev/null \
    || curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker --version

say "The jap user"
id -u "$USER_NAME" > /dev/null 2>&1 || useradd --create-home --shell /bin/bash "$USER_NAME"
usermod -aG docker "$USER_NAME"
# Root's keys let you in; give the same ones to jap, so deploys do not need root.
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" /root/.ssh/authorized_keys "/home/$USER_NAME/.ssh/authorized_keys"
fi

say "Firewall"
# SSH first, then enable: the other order locks you out of your own machine.
ufw allow OpenSSH
ufw default deny incoming
ufw default allow outgoing
ufw --force enable
ufw status verbose

say "Unattended security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now unattended-upgrades

say "Swap"
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile > /dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
free -h | head -3

say "SSH hardening"
# Password logins off, root login by key only. Written as a drop-in so an upgrade of
# sshd_config does not quietly undo it.
cat > /etc/ssh/sshd_config.d/10-jap.conf << 'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
SSHD
sshd -t && systemctl reload ssh 2> /dev/null || systemctl reload sshd

say "Checkout"
install -d -o "$USER_NAME" -g "$USER_NAME" "$(dirname "$DIR")"
if [ ! -d "$DIR/.git" ]; then
  sudo -u "$USER_NAME" git clone "$REPO" "$DIR"
fi
sudo -u "$USER_NAME" git -C "$DIR" fetch --tags --prune
chown -R "$USER_NAME:$USER_NAME" "$DIR"

cat << DONE

== Done. What is left, as the jap user (ssh $USER_NAME@this-machine):

  cd $DIR
  make env ENV=live          writes .env.live with a fresh database password and Inngest keys

Then put these into .env.live by hand, once, and nowhere else:
  OPENROUTER_API_KEY=        the key live bills AI to
  SMTP_PASSWORD=             the mailbox JAP sends from and reads security codes in
  TUNNEL_TOKEN=              Cloudflare Zero Trust > Networks > Tunnels, this server's own
                             tunnel, not the Mac's
  SENTRY_DSN=                optional (PRD O15)

  make deploy ENV=live TAG=vX.Y.Z

Then in the Cloudflare dashboard point the public hostname at http://ui:8080, which is Caddy
on the stack's own Docker network. The server opens no inbound port for it.
DONE
