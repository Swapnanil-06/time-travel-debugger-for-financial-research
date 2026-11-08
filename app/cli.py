"""Terminal demo and run-bundle tools. Run with python -m app.cli --help."""
import argparse
import json
import sys
import tempfile
from decimal import Decimal, localcontext
from pathlib import Path

from .bundle import verify_bundle
from .engine import ROOT, ResearchError
from .store import Store, seed


def demo():
    with tempfile.TemporaryDirectory(prefix="research-replay-") as temp:
        store = Store(Path(temp) / "demo.sqlite3")
        seed(store)
        runs = list(reversed(store.list()))
        original, correction = runs[:2]
        comparison = store.compare(original["id"], correction["id"])
        change = comparison["data_changes"][0]
        with localcontext() as ctx:
            ctx.prec = 34
            delta = (Decimal(correction["output"]["forecast"][-1]) - Decimal(original["output"]["forecast"][-1])) * 100
        print("RESEARCH REPLAY | An investigation in four steps\n")
        print(f"1. CAPTURE  {original['observations']} real DGS10 observations; as-of {original['as_of']}.")
        print(f"2. COMPARE  Injected correction on {change['date']}: {change['before']}% -> {change['after']}%.")
        print(f"            Identical code and parameters; T+5 forecast difference: {delta:+.2f} bp.")
        result = store.replay(original["id"])
        print(f"3. REPLAY   Original output reproduced: {result['actual_sha256']}")
        store.approve(original["id"])
        release = store.release(original["id"], correction["id"])
        assert not release["allowed"]
        assert store.release(original["id"], original["id"])["allowed"]
        print(f"4. GATE     Corrected candidate BLOCKED ({', '.join(release['mismatches'])}).")
        print("            Identical approved candidate PASSED.\n")
        print("Injected scenario, not a real FRED revision. No predictive-performance claim.")


def main():
    parser = argparse.ArgumentParser(description="Capture, compare and replay financial research.")
    parser.add_argument("--db", default=str(ROOT / ".replay/research.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run a self-contained four-step investigation in a temporary database")
    commands.add_parser("list", help="List captured runs")
    replay = commands.add_parser("replay", help="Replay a captured run")
    replay.add_argument("run_id")
    export = commands.add_parser("export", help="Export a self-contained JSON artifact bundle")
    export.add_argument("run_id")
    export.add_argument("output", type=Path)
    verify = commands.add_parser("verify", help="Verify and re-execute an exported bundle without its database")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            return demo()
        if args.command == "verify":
            result = verify_bundle(json.loads(args.bundle.read_text("utf-8")))
        else:
            store = Store(args.db)
            seed(store)
            if args.command == "list":
                for run in reversed(store.list()):
                    print(f"{run['id']}  {run['label']}  ({run['model']})")
                return
            if args.command == "replay":
                result = store.replay(args.run_id)
            else:
                run = store.get(args.run_id)
                args.output.write_text(json.dumps({"format": "research-replay-bundle-v1", "run": run}, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"Exported {run['id']} to {args.output}")
                return
        print(json.dumps(result, indent=2))
    except (ResearchError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
