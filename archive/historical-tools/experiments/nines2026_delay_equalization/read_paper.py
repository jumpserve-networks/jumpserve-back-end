#!/usr/bin/env python3
"""Print specified downloaded PDF pages and optionally render them for inspection.

This tool deliberately does not change the manifest's reading status. Extraction
alone is not a review. Example: read_paper.py 4 --pages 1-6 --render 6
"""
import argparse
from pathlib import Path
import subprocess
import pymupdf

ROOT = Path(__file__).resolve().parent


def numbers(value: str, count: int) -> list[int]:
    result = []
    for segment in value.split(','):
        bounds = [int(x) for x in segment.split('-')]
        if len(bounds) == 1:
            bounds *= 2
        if len(bounds) != 2 or not 1 <= bounds[0] <= bounds[1] <= count:
            raise ValueError(f'Invalid page range: {segment}')
        result.extend(range(bounds[0], bounds[1]+1))
    return sorted(set(result))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=int)
    parser.add_argument('--pages')
    parser.add_argument('--render')
    parser.add_argument('--ocr', action='store_true', help='OCR scanned pages with local Tesseract; inspect equations visually')
    args = parser.parse_args()
    with pymupdf.open(ROOT/'literature'/f'ref-{args.reference:02}.pdf') as document:
        if args.pages:
            for number in numbers(args.pages, len(document)):
                print(f'===== REFERENCE {args.reference}, PAGE {number}/{len(document)} =====')
                extracted = document[number-1].get_text()
                if not extracted.strip() and args.ocr:
                    target = ROOT/'literature'/'pages'
                    target.mkdir(exist_ok=True)
                    text_path = target/f'ref-{args.reference:02}-{number:02}-ocr.txt'
                    if not text_path.exists():
                        image_path = target/f'ref-{args.reference:02}-{number:02}.png'
                        document[number-1].get_pixmap(dpi=180).save(image_path)
                        result = subprocess.run(['tesseract', str(image_path), 'stdout'],
                                                capture_output=True, text=True, check=True)
                        text_path.write_text(result.stdout)
                    extracted = '[OCR transcription; equations may contain recognition errors]\n'+text_path.read_text()
                print(extracted or '[No extractable text: render this page or use --ocr]')
        if args.render:
            target = ROOT/'literature'/'pages'
            target.mkdir(exist_ok=True)
            for number in numbers(args.render, len(document)):
                path = target/f'ref-{args.reference:02}-{number:02}.png'
                document[number-1].get_pixmap(dpi=120).save(path)
                print(path)


if __name__ == '__main__':
    main()
