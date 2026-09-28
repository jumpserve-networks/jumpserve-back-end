#!/usr/bin/env python3
"""Download explicitly catalogued public research sources; never mark them read.

Run from any directory with the research venv. PDFs/text are local ignored
artifacts; the JSON manifest records URLs, hashes, lengths and retrieval errors.
Existing valid downloads are reused. No authentication or paywall workarounds.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

import pymupdf

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "literature.json"


def retrieve(entry: dict) -> dict:
    entry = dict(entry)
    if not entry.get("download_urls"):
        return entry
    destination = ROOT / "literature" / f"ref-{entry['number']:02}.pdf"
    errors = []
    for url in entry["download_urls"]:
        try:
            if not destination.exists():
                # curl uses the host trust store, unlike a bare Python macOS venv.
                # TLS verification stays enabled. Arguments never enter a shell.
                temporary = destination.with_suffix(".download")
                response = subprocess.run([
                    "curl", "--fail", "--location", "--silent", "--show-error",
                    "--max-time", "45", "--max-filesize", str(50 * 1024 * 1024),
                    "--proto", "=https,http", "--proto-redir", "=https,http",
                    "--output", str(temporary), "--write-out", "%{url_effective}", url,
                ], capture_output=True, text=True)
                if response.returncode:
                    temporary.unlink(missing_ok=True)
                    raise ValueError(response.stderr.strip())
                data = temporary.read_bytes()
                temporary.unlink()
                resolved_url = response.stdout
                if len(data) > 50 * 1024 * 1024 or not data.startswith(b"%PDF"):
                    raise ValueError("Response is not a PDF or exceeds 50 MiB")
                destination.write_bytes(data)
            else:
                data = destination.read_bytes()
                resolved_url = entry.get("resolved_url", url)
            with pymupdf.open(destination) as reader:
                pages = [page.get_text() for page in reader]
            textfile = destination.with_suffix(".txt")
            textfile.write_text("\n\n".join(f"===== PAGE {i+1} =====\n{p}" for i, p in enumerate(pages)))
            entry.update(download_status="downloaded", resolved_url=resolved_url,
                         sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                         pages=len(pages), text_characters=sum(map(len, pages)),
                         retrieved_at=entry.get("retrieved_at", datetime.now(timezone.utc).isoformat()))
            entry.pop("retrieval_errors", None)
            print(f"ref {entry['number']:02}: {len(pages)} pages", flush=True)
            return entry
        except (OSError, ValueError) as exc:
            errors.append({"url": url, "error": str(exc)[:240]})
        except Exception as exc:
            # A failed or malformed source must not abort other reference downloads.
            errors.append({"url": url, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    entry.update(download_status="unavailable", retrieval_errors=errors)
    print(f"ref {entry['number']:02}: unavailable", flush=True)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", nargs="*", type=int)
    args = parser.parse_args()
    entries = json.loads(MANIFEST.read_text())
    selected = [e for e in entries if args.references is None or e["number"] in args.references]
    (ROOT / "literature").mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        updates = {e["number"]: e for e in pool.map(retrieve, selected)}
    MANIFEST.write_text(json.dumps([updates.get(e["number"], e) for e in entries], indent=2) + "\n")


if __name__ == "__main__":
    main()
