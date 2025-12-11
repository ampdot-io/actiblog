#!/bin/bash
set -e

# Hugo version to install
HUGO_VERSION="0.145.0"
HUGO_DEB="hugo_extended_${HUGO_VERSION}_linux-amd64.deb"
HUGO_URL="https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/${HUGO_DEB}"

echo "Installing Hugo v${HUGO_VERSION}..."

# Download the .deb file
curl -L -o "/tmp/${HUGO_DEB}" "${HUGO_URL}"

# Install using dpkg
sudo dpkg -i "/tmp/${HUGO_DEB}"

# Verify installation
hugo version

echo "Hugo v${HUGO_VERSION} installed successfully!"
