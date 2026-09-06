"""Stage the drift demo: make the vault stale, so a real sweep has to catch up.

The vault is edited to claim the S Pass floor is still S$3,300 and the snapshot
is rewritten to match, so the live MOM page -- which says S$3,600 -- genuinely
disagrees with us. Nothing about the government page is faked; the staleness is
on our side, which is the only half we are allowed to fake.

    python demo_drift.py            stage it
    python run.py sweep --only s-pass-qualifying-salary-2027
    python run.py rollback s-pass-qualifying-salary-2027
    python demo_drift.py --restore  put the vault back

--restore is also the panic button if a demo run goes sideways on camera.
"""
import hashlib
import json
import pathlib
import re
import shutil
import sys

SLUG = "s-pass-qualifying-salary-2027"
RULE = pathlib.Path(f"vault/rules/{SLUG}.md")
SNAP = pathlib.Path(f"vault/.snapshots/{SLUG}.json")
SAVE = pathlib.Path(".demo-backup")
STALE = {"title: S Pass qualifying salary rises to S$3,600 from 1 January 2027":
         "title: S Pass qualifying salary rises to S$3,300 from 1 January 2027",
         "threshold_before: 3300": "threshold_before: 3000",
         "threshold_after: 3600": "threshold_after: 3300"}


def staged() -> list:
    return sorted(SAVE.iterdir()) if SAVE.is_dir() else []


def restore() -> int:
    files = staged()
    if not files:
        print("nothing staged -- no .demo-backup/ to restore from")
        return 1
    for f in files:
        shutil.copy2(f, (SNAP if f.suffix == ".json" else RULE).parent / f.name)
        f.unlink()
    # OneDrive keeps a handle on the directory often enough that rmdir fails;
    # the backup is empty by now, so an undeletable husk is harmless
    try:
        SAVE.rmdir()
    except OSError:
        pass
    shutil.rmtree(pathlib.Path(f"vault/.history/{SLUG}"), ignore_errors=True)
    print(f"restored {RULE} and its snapshot; .history/{SLUG} cleared")
    return 0


def stage() -> int:
    if staged():
        print("already staged -- run --restore first")
        return 1
    if not SNAP.is_file():
        print(f"no snapshot yet. Run: python run.py sweep --only {SLUG}")
        return 1

    SAVE.mkdir(exist_ok=True)
    shutil.copy2(RULE, SAVE)
    shutil.copy2(SNAP, SAVE)

    text = RULE.read_text(encoding="utf-8")
    for old, new in STALE.items():
        if old not in text:
            restore()
            print(f"rule does not look like the shipped one, missing: {old!r}")
            return 1
        text = text.replace(old, new, 1)
    RULE.write_text(text, encoding="utf-8")

    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    snap["region"] = snap["region"].replace("$3,600", "$3,300")
    snap["hash"] = hashlib.sha256(
        re.sub(r"\s+", " ", snap["region"]).strip().encode()).hexdigest()[:16]
    SNAP.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    print(f"staged. The vault now claims S$3,300; MOM still says S$3,600.\n"
          f"  python run.py sweep --only {SLUG}")
    return 0


if __name__ == "__main__":
    sys.exit(restore() if "--restore" in sys.argv[1:] else stage())
