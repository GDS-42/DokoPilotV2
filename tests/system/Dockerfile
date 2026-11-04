# SPDX-License-Identifier: AGPL-3.0-or-later
# Debian 13 test server for tests/system/run.sh: systemd as PID 1, OpenSSH, Python and sudo,
# with the accounts a fresh VPS or cloud image has: a "provider" account with password-less
# sudo, and a maintenance account already created by cloud-init.
FROM debian:trixie

# Optional HTTPS proxy and extra CA, for hosts that reach the Internet only through a proxy
# (set by run.sh; empty otherwise). apt then uses https sources through the proxy.
ARG APT_HTTPS_PROXY=""
COPY extra-ca.crt /usr/local/share/ca-certificates/dokopilot-test-extra-ca.crt

RUN set -eux; \
    if [ -n "$APT_HTTPS_PROXY" ]; then \
      sed -i 's#http://deb.debian.org#https://deb.debian.org#g' /etc/apt/sources.list.d/debian.sources; \
      printf 'Acquire::https::Proxy "%s";\n' "$APT_HTTPS_PROXY" > /etc/apt/apt.conf.d/99dokopilot-test-proxy; \
    fi; \
    if [ -s /usr/local/share/ca-certificates/dokopilot-test-extra-ca.crt ]; then \
      printf 'Acquire::https::CAInfo "/usr/local/share/ca-certificates/dokopilot-test-extra-ca.crt";\n' \
        > /etc/apt/apt.conf.d/98dokopilot-test-ca; \
    else \
      rm -f /usr/local/share/ca-certificates/dokopilot-test-extra-ca.crt; \
    fi; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus openssh-server openssh-client python3 sudo ca-certificates \
      iproute2 procps curl; \
    rm -f /etc/apt/apt.conf.d/98dokopilot-test-ca; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Units that cannot work in a container, and the apt timers that would race with Ansible.
RUN systemctl mask systemd-modules-load.service apt-daily.timer apt-daily-upgrade.timer \
      getty@tty1.service console-getty.service

RUN set -eux; \
    useradd --create-home --shell /bin/bash provider; \
    useradd --create-home --shell /bin/bash --groups sudo --comment "Maintenance DokoPilot" integrateur; \
    printf '%s\n' \
      '# Created by cloud-init (emulated by the DokoPilot system test)' \
      '# User rules for provider' \
      'provider ALL=(ALL) NOPASSWD:ALL' \
      '# User rules for integrateur' \
      'integrateur ALL=(ALL) NOPASSWD:ALL' \
      '# Unrelated rule that must be kept' \
      'backup ALL=(root) NOPASSWD: /usr/bin/true' \
      > /etc/sudoers.d/90-cloud-init-users; \
    chmod 0440 /etc/sudoers.d/90-cloud-init-users; \
    visudo -cf /etc/sudoers.d/90-cloud-init-users

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
