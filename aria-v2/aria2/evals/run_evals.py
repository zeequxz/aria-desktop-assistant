"""evals/run_evals.py - CLI to run an eval suite and write a report.

    python -m aria2.evals.run_evals                 # run the "all" suite (needs key)
    python -m aria2.evals.run_evals --suite coder    # run a per-agent suite
    python -m aria2.evals.run_evals --stub           # keyless self-test of the harness

Writes a JSON report under %APPDATA%/ARIA2/evals/ (via evals.store) and prints a
summary table so regressions show up as a dropping pass-rate over time.
"""

from __future__ import annotations

import sys

from aria2.core import db


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    db.init()

    if "--stub" in argv:
        from aria2.evals.harness import self_test

        res = self_test()
        ok = res["pass_ok"] and res["fail_ok"]
        print(f"harness self-test: pass-case={res['pass_ok']} fail-case={res['fail_ok']}"
              f" -> {'OK' if ok else 'FAILED'}")
        return 0 if ok else 1

    suite = "all"
    if "--suite" in argv:
        i = argv.index("--suite")
        if i + 1 < len(argv):
            suite = argv[i + 1]

    from aria2.evals import store
    from aria2.evals.cases import get_suite
    from aria2.evals.harness import run_suite

    cases = get_suite(suite)
    print(f"Running suite '{suite}' ({len(cases)} cases)...\n")
    summary = run_suite(cases)
    for r in summary["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['id']:<18} score={r['score']:.2f} "
              f"status={r['status']} ${r['cost_usd']:.4f} {r['elapsed_ms']}ms")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"          x failed check: {c['check']}")
    print(f"\nPass rate: {summary['passed']}/{summary['total']} "
          f"({summary['pass_rate']:.0%})  ·  total ${summary['cost_usd']:.4f}")
    print(f"Report: {store.save_report(summary, suite)}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
