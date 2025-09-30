#!/bin/bash

# Deployment script for VPS
# Run this script on your VPS server

set -e

PROJECT_DIR="/var/www/logist2"
VENV_DIR="$PROJECT_DIR/venv"
REPO_URL="your-git-repo-url"  # Replace with your git repository

echo "=== Starting deployment ==="

# Update code from git
echo "Pulling latest code..."
cd $PROJECT_DIR
git pull origin main  # or master

# Activate virtual environment
echo "Activating virtual environment..."
source $VENV_DIR/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Run migrations
echo "Running database migrations..."
python manage.py migrate --noinput

# Restart services
echo "Restarting services..."
sudo systemctl restart logist2
sudo systemctl restart nginx

echo "=== Deployment completed successfully ==="

