"""Ante's entry point. One command for both graphs.

    python run.py ask "what changes for us in the next 90 days?"
    python run.py sweep --dry-run
    python run.py sweep --only s-pass-qualifying-salary-2027
    python run.py rollback s-pass-qualifying-salary-2027
    python run.py negative

`sweep` needs AWS only when a source page has actually moved; on a quiet day it
finishes on pure Python and costs nothing. `ask` always needs it, so it checks
credentials before spending a turn discovering they are dead.
"""
import argparse
import sys

from ante.model import credentials_ok

sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="ask the advisor a question")
    a.add_argument("question")
    a.add_argument("--thread", help="checkpoint id (default: the company UEN)")

    s = sub.add_parser("sweep", help="run the curator over every rule")
    s.add_argument("--dry-run", action="store_true", help="decide, write nothing")
    s.add_argument("--only", metavar="SLUG", help="one rule, by filename stem")

    r = sub.add_parser("rollback", help="undo the last auto-applied change")
    r.add_argument("slug")

    sub.add_parser("negative", help="prove a fabricated quote cannot be written")

    b = sub.add_parser("brief", help="sweep, then email the founder if anything moved")
    b.add_argument("--force", action="store_true",
                   help="send even when nothing has changed since the last brief")

    v = sub.add_parser("serve", help="local API for the web UI (127.0.0.1 only)")
    v.add_argument("--port", type=int, default=8000)

    args = p.parse_args()

    if args.cmd == "serve":
        import uvicorn
        import vault
        from ante.api import WEB
        print(f"vault  {vault.VAULT}")
        # the board is the thing to open, so it is named first and named at all
        # -- a server that only prints /docs invites a demo of the wrong screen
        print(f"board  http://127.0.0.1:{args.port}/"
              if WEB.is_dir() else "board  (no web/ directory -- API only)")
        print(f"docs   http://127.0.0.1:{args.port}/docs")
        uvicorn.run("ante.api:app", host="127.0.0.1", port=args.port)
        return 0

    if args.cmd == "brief":
        from ante.notify import brief
        out = brief(force=args.force)
        if out.get("quiet"):
            return print(f"nothing new since the last brief "
                         f"({out['findings']} findings unchanged) -- no email sent") or 0
        if "error" in out:
            return print(f"brief not sent: {out['error']}") or 1
        where = out.get("sent") or out["outbox"]
        print(f"brief -> {where}  ({out['findings']} findings)")
        if "outbox" in out:
            print(f"  no email sent: {out['why']}")
        return 0

    if args.cmd == "ask":
        from ante.advisor import ask, show
        alive, detail = credentials_ok()
        if not alive:
            return print(f"bedrock unavailable: {detail}") or 1
        show(ask(args.question, thread_id=args.thread))
        return 0

    if args.cmd == "sweep":
        from ante.curator import sweep
        out = sweep(dry_run=args.dry_run, only=args.only)
        if "metrics" not in out:
            return print(out["error"]) or 1
        print(f"\n{out['metrics'].report()}")
        return 0

    if args.cmd == "rollback":
        from ante.apply import rollback
        res = rollback(args.slug)
        print(res)
        return int("error" in res)

    from ante.curator import negative_test
    return int(not negative_test())


if __name__ == "__main__":
    sys.exit(main())
