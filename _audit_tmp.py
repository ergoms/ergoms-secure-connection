import re
from pathlib import Path

root = Path(__file__).resolve().parent
SKIP = {".venv", "dist", "__pycache__", ".git"}


def keep(p: Path) -> bool:
    parts = set(p.parts)
    return not (parts & SKIP) and p.name != "_audit_tmp.py"


py_files = [p for p in root.rglob("*.py") if keep(p)]

patterns = {
    "except Exception:": r"except Exception\s*:",
    "except pass": r"except [^\n]+:\n(?:[^\n]*\n){0,3}?\s+pass\b",
    "print(": r"\bprint\s*\(",
    "TODO/FIXME/HACK": r"\b(TODO|FIXME|HACK|XXX)\b",
    "legacy/deprecated": r"\b(legacy|deprecated|backward compat|for backward)\b",
    "sys.platform/os.name": r"\b(sys\.platform|os\.name)\b",
    "subprocess.": r"\bsubprocess\.",
    "time.sleep(": r"\btime\.sleep\s*\(",
}


def count_in_file(path, pat, flags=0):
    text = path.read_text(encoding="utf-8", errors="replace")
    return len(re.findall(pat, text, flags))


for name, pat in patterns.items():
    flags = re.I if "legacy" in name or "TODO" in name else 0
    rows = []
    for p in sorted(py_files):
        c = count_in_file(p, pat, flags)
        if c:
            rows.append((c, str(p.relative_to(root)).replace("\\", "/")))
    total = sum(c for c, _ in rows)
    print(f"=== {name} total={total} files={len(rows)} ===")
    for c, f in sorted(rows, reverse=True)[:25]:
        print(f"  {c:4d}  {f}")
    if len(rows) > 25:
        print(f"  ... {len(rows)-25} more files")
    print()

# God functions in lib/
print("=== lib god functions (>60 lines) ===")
for p in sorted((root / "lib").glob("*.py")):
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    func = None
    start = 0
    depth = 0
    for i, line in enumerate(lines, 1):
        m = re.match(r"^(async )?def (\w+)", line)
        if m and (depth == 0 or line.startswith("def ") or line.startswith("async def ")):
            if func and i - start > 60:
                print(f"  {p.name}:{func} lines {start}-{i-1} ({i-start} lines)")
            func = m.group(2)
            start = i
    if func and len(lines) - start + 1 > 60:
        print(f"  {p.name}:{func} lines {start}-{len(lines)} ({len(lines)-start+1} lines)")
