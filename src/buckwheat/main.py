"""Console script entry point for `buckwheat`."""

import sys

from buckwheat.cli import run


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
