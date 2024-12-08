#!/bin/bash

# Activate the virtual environment
source venv/bin/activate

# Start the bot
echo "Starting the bot..."
python3 main.py

# Deactivate the virtual environment when done
deactivate
