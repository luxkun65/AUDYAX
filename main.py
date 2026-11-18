"""Starts AUDYAX (graphical interface).

    python main.py
    python main.py --self-test result.json     (checks the program, used by CI)
"""
from audyax.gui.app import main

if __name__ == "__main__":
    main()
