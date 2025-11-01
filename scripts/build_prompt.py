import os
from pathlib import Path

# ── Configure your file/folder list here (files OR folders) ────────────────────
FILE_PATHS = [
    "src",                     # whole tree
    "tests",                   # another tree
    "app.py",                  # individual files still work
    "app_factory.py",
    "scripts",
    "static",
    "templates"
]

# Optional knobs
USE_BASENAME = True   # If True, markers show just the filename; otherwise full path
MAX_CHARS    = None   # e.g., 20000 to cap output length; None to disable

# Only these extensions are allowed
ALLOWED_EXTS = {
    ".py": "python",
    ".js": "javascript",
    ".html": "html",
}

# Optionally skip noisy dirs when scanning folders (names, not paths)
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    "node_modules", "dist", "build",
}

# ── Helpers ────────────────────────────────────────────────────────────────────
def dynamic_backtick_fence(text: str, min_ticks: int = 3) -> str:
    """Choose a fence longer than any run of backticks in the text."""
    max_run = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    return "`" * max(max_run + 1, min_ticks)

def read_text(path: Path) -> str:
    """Read file safely as UTF-8, replacing undecodable bytes instead of crashing."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"[Error reading {path}: {e}]"

def build_block(path_str: str) -> str:
    p = Path(path_str)
    ext = p.suffix.lower()
    if ext not in ALLOWED_EXTS:
        label = p.name if USE_BASENAME else str(p)
        return f"===== SKIPPED (extension not allowed): {label} ====="

    label = p.name if USE_BASENAME else str(p)
    body = read_text(p)
    fence = dynamic_backtick_fence(body)
    lang = ALLOWED_EXTS[ext]

    header = f"===== BEGIN FILE: {label} ====="
    footer = f"===== END FILE: {label} ====="
    info   = f"(path: {p})"

    return (
        f"{header}\n"
        f"{info}\n"
        f"{fence}{lang}\n"
        f"{body}\n"
        f"{fence}\n"
        f"{footer}\n"
    )

def iter_allowed_files_in_dir(root: Path):
    """Yield allowed files under 'root' recursively, skipping EXCLUDE_DIRS."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded dirs in-place for efficiency
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.suffix.lower() in ALLOWED_EXTS:
                files.append(fp)
    # deterministic order
    files.sort(key=lambda p: str(p).lower())
    return files

def merge_files(paths):
    """Return (merged_text, included_files)."""
    blocks = []
    included_files = []

    for raw in paths:
        p = Path(raw)

        if p.is_dir():
            files = iter_allowed_files_in_dir(p)
            if not files:
                label = p.name if USE_BASENAME else str(p)
                blocks.append(f"===== EMPTY FOLDER (no allowed files): {label} =====")
                continue
            for fp in files:
                included_files.append(fp)
                blocks.append(build_block(str(fp)))
            continue

        if p.is_file():
            if p.suffix.lower() in ALLOWED_EXTS:
                included_files.append(p)
            blocks.append(build_block(str(p)))
            continue

        # missing path (file or folder)
        label = os.path.basename(raw) if USE_BASENAME else raw
        blocks.append(f"===== MISSING PATH: {label} =====")

    merged = "\n".join(blocks)
    if isinstance(MAX_CHARS, int) and MAX_CHARS > 0 and len(merged) > MAX_CHARS:
        merged = merged[:MAX_CHARS] + "\n[...TRUNCATED...]"
    return merged, included_files

# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    merged_text, included = merge_files(FILE_PATHS)

    # Print the included files list first
    print(f"Included files ({len(included)}):")
    for i, fp in enumerate(included, 1):
        print(f"{i:3}. {fp}")

    print("\n" + "-" * 60 + "\n")

    out_path = "merged_context.txt"
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(merged_text)
    print(f"\n✅ Merged context saved to {out_path}")