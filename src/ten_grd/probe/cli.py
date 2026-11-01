"""ten-grd command line.

    ten-grd probe init                 write tenguard_probe.py, a harness to fill in
    ten-grd probe run                  probe for cross-tenant leaks
        --harness tenguard_probe.py --out leak-report.md --json leak-report.json

Exit codes for probe run: 0 no leak found, 1 leak found, 2 inconclusive or harness error.
"""
import argparse
import json
import sys
from pathlib import Path

from .harness import TEMPLATE, HarnessError, load_harness
from .report import markdown
from .runner import run_probe

EXIT = {"clean": 0, "leak": 1, "inconclusive": 2}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ten-grd", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="find cross-tenant data leaks in your agent")
    actions = probe.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="write a harness template")
    init.add_argument("--path", default="tenguard_probe.py")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    run = actions.add_parser("run", help="run the probe")
    run.add_argument("--harness", default="tenguard_probe.py")
    run.add_argument("--out", default="leak-report.md", help="Markdown report path")
    run.add_argument("--json", dest="json_out", help="also write the JSON record here")
    args = parser.parse_args(argv)

    if args.action == "init":
        path = Path(args.path)
        if path.exists() and not args.force:
            print(f"{path} already exists; use --force to overwrite.", file=sys.stderr)
            return 2
        path.write_text(TEMPLATE, encoding="utf-8")
        print(f"Wrote {path}. Fill in sign_in(), seed() and ask(), then run: ten-grd probe run")
        return 0

    try:
        harness = load_harness(args.harness)
    except HarnessError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Probing {len(harness.tenants)} tenants: {', '.join(harness.tenants)} "
          "(test environments only)")
    result = run_probe(harness)
    Path(args.out).write_text(markdown(result), encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    for finding in result["findings"]:
        print(f"  [LEAK] signed in as {finding['as']}, saw {finding['leaked_from']}'s data via {finding['attack']}")
    print(f"{result['verdict'].upper()}: {result['summary']}")
    print(f"Report: {args.out}")
    return EXIT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
