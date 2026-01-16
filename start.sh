#!/bin/bash

# Kill any process running on port 8000
echo "Killing processes on port 8000..."
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
sleep 1

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies if needed
if [ ! -f "venv/.deps_installed" ]; then
    echo "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    touch venv/.deps_installed
fi

# Create storage directories
mkdir -p storage/uploads
mkdir -p storage/recordings

# Check .env file
if [ ! -f ".env" ]; then
    echo "WARNING: .env file not found!"
    echo "Please copy .env.example to .env and configure it."
    echo ""
    read -p "Press enter to continue anyway..."
fi

# Start the server
echo "Starting backend server on port 8000..."
echo "API Docs: http://localhost:8000/docs"
echo "Press Ctrl+C to stop the server"
echo ""
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

