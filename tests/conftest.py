import os
import sys

# make "src" importable even without an editable install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("MPLBACKEND", "Agg")
