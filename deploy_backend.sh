#!/bin/bash
# Market Wisdom Backend Setup Script for Ubuntu 22.04 LTS

echo "=========================================="
echo "Starting Market Wisdom VPS Server Setup..."
echo "=========================================="

# 1. Update OS and establish python environment
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx sqlite3 libcanberra-gtk-module libcanberra-gtk3-module

# 2. Setup project folder
mkdir -p /opt/marketwisdom
cd /opt/marketwisdom

# 3. Create virtual environment so we don't break system python
python3 -m venv venv
source venv/bin/activate

# 4. We assume the user has copied the 'backend' folder via SFTP/Git
# We pretend backend files are here. Let's install requirements.
if [ -f "backend/requirements.txt" ]; then
    pip install -r backend/requirements.txt
    pip install gunicorn
    # Install Playwright browser
    playwright install chromium
    playwright install-deps chromium
else
    echo "Warning: backend folder not found yet."
fi

# 5. Create SystemD service so Gunicorn runs on startup
sudo cat <<EOF > /etc/systemd/system/marketwisdom.service
[Unit]
Description=Gunicorn instance to serve Market Wisdom API
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/opt/marketwisdom/backend
Environment="PATH=/opt/marketwisdom/venv/bin"
# Set your Gemini API key here later:
Environment="GEMINI_API_KEY="
ExecStart=/opt/marketwisdom/venv/bin/gunicorn --workers 3 --bind 0.0.0.0:5000 -m 007 app:app

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl start marketwisdom
sudo systemctl enable marketwisdom
sudo systemctl status marketwisdom

echo "================================================================"
echo "Setup Complete! The server is configured."
echo "Remember to sync your 'backend' folder here to /opt/marketwisdom"
echo "and set your GEMINI_API_KEY inside the systemd marketwisdom.service file."
echo "================================================================"
