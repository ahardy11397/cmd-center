#!/usr/bin/env bash
# setup_v4l2loopback.sh — Configures Linux virtual webcam device for CMD Center
# Supports both Debian-family (apt) and Arch-family (pacman) systems.
set -euo pipefail

VIDEO_NR="${1:-10}"
CARD_LABEL="${2:-Tablet Webcam}"

echo "=== CMD Center Virtual Webcam Setup ==="
echo "Target device: /dev/video${VIDEO_NR} (${CARD_LABEL})"

# Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run with sudo or as root:"
    echo "  sudo $0 [video_nr] [card_label]"
    exit 1
fi

# 1. Install prerequisites if missing (distro-aware)
if command -v apt-get >/dev/null 2>&1; then
    if ! dpkg -s v4l2loopback-dkms >/dev/null 2>&1; then
        echo "--> Installing v4l2loopback-dkms and v4l2loopback-utils (apt)..."
        apt-get update
        apt-get install -y v4l2loopback-dkms v4l2loopback-utils
    else
        echo "--> v4l2loopback-dkms is already installed."
    fi
elif command -v pacman >/dev/null 2>&1; then
    if ! pacman -Q v4l2loopback-dkms-git >/dev/null 2>&1 && ! pacman -Q v4l2loopback-dkms >/dev/null 2>&1; then
        echo "--> Installing v4l2loopback-dkms (pacman)..."
        pacman -Sy --noconfirm v4l2loopback-dkms
    else
        echo "--> v4l2loopback-dkms is already installed."
    fi
else
    echo "--> No apt-get or pacman found; assuming module is already available."
fi

# Verify the module is actually built for the running kernel
if ! modinfo v4l2loopback >/dev/null 2>&1; then
    echo "ERROR: v4l2loopback module not found for kernel $(uname -r)."
    echo "       Reinstall the dkms package or rebuild: dkms autoinstall"
    exit 1
fi
echo "--> v4l2loopback module present for kernel $(uname -r)."

# 2. Unload module if loaded with different parameters
if lsmod | grep -q "^v4l2loopback "; then
    echo "--> Unloading existing v4l2loopback module..."
    modprobe -r v4l2loopback || true
fi

# 3. Load module with optimal settings for browser & video call compatibility
# exclusive_caps=1 is required for Chrome/Firefox/Zoom to recognize it as a webcam
echo "--> Loading v4l2loopback module..."
modprobe v4l2loopback \
    devices=1 \
    video_nr="${VIDEO_NR}" \
    card_label="${CARD_LABEL}" \
    exclusive_caps=1 \
    max_buffers=2

# 4. Make persistent across reboots
echo "--> Configuring persistence across reboots..."
cat <<EOF > /etc/modules-load.d/v4l2loopback.conf
v4l2loopback
EOF

cat <<EOF > /etc/modprobe.d/v4l2loopback.conf
options v4l2loopback devices=1 video_nr=${VIDEO_NR} card_label="${CARD_LABEL}" exclusive_caps=1 max_buffers=8
EOF

# 5. Verify device
if [ -e "/dev/video${VIDEO_NR}" ]; then
    echo "--> SUCCESS: /dev/video${VIDEO_NR} is created and ready!"
    chmod 666 "/dev/video${VIDEO_NR}" || true
    ls -l "/dev/video${VIDEO_NR}"
else
    echo "--> WARNING: /dev/video${VIDEO_NR} was not found. Check 'dmesg | grep v4l2loopback'."
    exit 1
fi

echo "=== Virtual webcam setup complete! ==="
