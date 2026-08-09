#!/bin/bash
echo "================================"
echo "  Installing PrintStation"
echo "================================"

# Copy files
mkdir -p ~/.printstation/static
cp app.py ~/.printstation/
cp static/index.html ~/.printstation/static/

# Install dependencies
echo "Installing dependencies..."
pip3 install flask flask-cors pypdf --break-system-packages -q

# Install systemd service
echo "Setting up auto-start service..."
sudo cp printstation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable printstation
sudo systemctl start printstation

# Add sudo rules for cupsenable (no password needed)
echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/cupsenable, /usr/bin/cupsaccept, /usr/bin/cupsreject, /usr/sbin/lpadmin, /usr/bin/systemctl restart cups" | sudo tee /etc/sudoers.d/printstation

# Create desktop launcher
cat > ~/.local/share/applications/printstation.desktop << 'DESKTOP'
[Desktop Entry]
Name=PrintStation
Comment=Print Management Dashboard
Exec=xdg-open http://localhost:5000
Icon=printer
Terminal=false
Type=Application
Categories=Office;
DESKTOP

echo "================================"
echo "  ✅ PrintStation Installed!"
echo "================================"
echo "  Open: http://localhost:5000"
echo "  Auto-starts on boot!"
echo "  Desktop: search 'PrintStation'"
echo "================================"

# Open browser
sleep 2
xdg-open http://localhost:5000 &
