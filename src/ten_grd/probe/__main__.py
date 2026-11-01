"""python -m ten_grd.probe ... is the same as the ten-grd command."""
import sys

from .cli import main

sys.exit(main(["probe", *sys.argv[1:]]))
