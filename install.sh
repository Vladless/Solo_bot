#!/bin/bash

# Check if the virtual environment directory exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate the virtual environment
source venv/bin/activate

# Install dependencies if they haven't been installed yet
if [ ! -f "venv/lib/python*/site-packages/installed" ]; then
    echo "Installing wheel for faster installing..."
    pip3 install wheel

    echo "Installing dependencies..."
    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt
    fi
fi

echo "Installation completed successfully!"
