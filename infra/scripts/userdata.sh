#!/bin/bash
# Reference EC2 user-data for Streamlit (also embedded in odc-stack.yaml via Fn::Sub).
# Variables substituted by CloudFormation: ApiBaseUrl, RepoUrl, RepoBranch, OdcTimezone
set -euxo pipefail
exec > >(tee /var/log/odc-userdata.log) 2>&1

dnf update -y
dnf install -y git python3.11 python3.11-pip python3.11-devel gcc

APP_ROOT=/opt/odc-meeting
mkdir -p "$APP_ROOT"

if [ ! -d "$APP_ROOT/repo/.git" ]; then
  git clone --depth 1 --branch "${RepoBranch}" "${RepoUrl}" "$APP_ROOT/repo"
else
  cd "$APP_ROOT/repo"
  git fetch origin "${RepoBranch}"
  git checkout "${RepoBranch}"
  git pull --ff-only
fi

cd "$APP_ROOT/repo/frontend"
python3.11 -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/pip" install --upgrade pip
"$APP_ROOT/venv/bin/pip" install -r requirements.txt

cat > /etc/systemd/system/odc-streamlit.service <<EOF
[Unit]
Description=ODC Meeting Room Streamlit UI
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=$APP_ROOT/repo/frontend
Environment=API_BASE_URL=${ApiBaseUrl}
Environment=ODC_TIMEZONE=${OdcTimezone}
# Arrow's bundled mimalloc segfaults in mi_thread_init on this AMI when it first
# allocates from a new thread, and Streamlit runs every rerun on a fresh thread —
# that took the whole server down mid-click. The system allocator avoids that code
# path; these payloads are tiny either way.
Environment=ARROW_DEFAULT_MEMORY_POOL=system
# User-data only runs at first boot, so refresh the checkout here to make a service
# restart enough to pick up a new commit. The leading "-" keeps a network blip or a
# bad fetch from blocking the UI from starting.
ExecStartPre=-/usr/bin/git -C $APP_ROOT/repo fetch --depth 1 origin ${RepoBranch}
ExecStartPre=-/usr/bin/git -C $APP_ROOT/repo reset --hard origin/${RepoBranch}
ExecStartPre=-$APP_ROOT/venv/bin/pip install -q -r $APP_ROOT/repo/frontend/requirements.txt
ExecStart=$APP_ROOT/venv/bin/streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --browser.gatherUsageStats false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chown -R ec2-user:ec2-user "$APP_ROOT"
systemctl daemon-reload
systemctl enable --now odc-streamlit.service
