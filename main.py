"""
main.py — WiFi Direct File Transfer
Entry point. Launches the Tkinter UI application.
"""

import sys
import os

# Ensure the project root is in Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.app import main

if __name__ == "__main__":
    main()
