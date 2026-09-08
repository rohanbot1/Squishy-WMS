#!/usr/bin/env bash
# Render build script for the single-service deployment: builds the
# frontend (frontend/dist), then installs the Python dependencies that
# actually run the app. Render's native Python build image doesn't ship
# Node.js, so this installs a pinned version via nvm first rather than
# assuming it's already present.
set -o errexit

export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi
\. "$NVM_DIR/nvm.sh"
nvm install 20
nvm use 20

cd frontend
npm ci
npm run build
cd ..

pip install -r requirements.txt
