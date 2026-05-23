"""GUI entry point."""

from .app import main as run_app


def main() -> int:
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
