"""Display-only paper titles from hash-verified extracted text, never filenames.

No PDF parsing, model invocation, persistence or source rewriting on a queue read.
PDF metadata must also occur on the first page. The conservative front-matter
fallback needs an identifiable author/affiliation boundary; ambiguity stays null.
"""
from functools import lru_cache
import json
import re
import time
import unicodedata
import store
from workflow import digest, identifier

VERSION = 'paper-display-title-v1'
MAX_PACKET_BYTES = 8_000_000

def clean(value):
    if not isinstance(value, str): return ''
    value = unicodedata.normalize('NFKC', value).replace('\u00ad', '')
    if any(unicodedata.category(c) == 'Cc' and not c.isspace() for c in value): return ''
    return ' '.join(value.split())

def valid(value):
    return (5 <= len(value) <= 300 and len(re.findall(r'[^\W\d_]+', value)) >= 2 and
        value.casefold() not in ('untitled document', 'uploaded paper', 'untitled', 'microsoft word') and
        not re.search(r'https?://|www\.|@|\.pdf\b|[<>]|_{2,}', value, re.I))

def author_line(value):
    words = value.replace('✉', '').replace('*', '').split()
    stop = {'a', 'an', 'the', 'to', 'of', 'on', 'and', 'for', 'with', 'in', 'from', 'across', 'is', 'are', 'how', 'why', 'using', 'via'}
    return (2 <= len(words) <= 4 and not any(w.casefold() in stop for w in words) and
        all(re.fullmatch(r"[^\W\d_][\w.'’\-]*", w, re.UNICODE) and w[0].isupper() for w in words))

def extract_title(packet):
    if not isinstance(packet, dict): return None
    pages = packet.get('pages')
    if not isinstance(pages, list) or not pages or not isinstance(pages[0], dict) or pages[0].get('page') != 1: return None
    raw = pages[0].get('text')
    if not isinstance(raw, str) or not raw.strip(): return None
    front = raw[:6000]
    # A metadata string appearing only in the abstract/body is not a title.
    front = re.split(r'(?im)^\s*(?:abstract|summary|1[. ]+introduction)\b', front, maxsplit=1)[0]
    metadata = packet.get('document_metadata')
    title = clean(metadata.get('title')) if isinstance(metadata, dict) else ''
    if valid(title) and title.casefold() in clean(front).casefold():
        return dict(title=title, method='PDF metadata corroborated by first-page text', page=1, version=VERSION)
    lines = [clean(line) for line in front.splitlines() if clean(line)]
    # Skip only explicit publisher/preprint banners, not arbitrary short lines.
    while lines and re.match(r'^(?:arXiv:|Proceedings of |Published as |Preprint\b|Accepted (?:at|for)\b|===== PAGE 1)', lines[0], re.I): lines.pop(0)
    for index, line in enumerate(lines[:12]):
        if re.match(r'^(?:abstract|summary|introduction|1[. ]+introduction)\b', line, re.I): break
        if not 1 <= index <= 4: continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ''
        affiliation = bool(re.search(r'\b(?:university|institute|department|laboratory|laboratories|college)\b', next_line, re.I))
        boundary = author_line(line) and (affiliation or '✉' in line)
        if not boundary: continue
        prefix = lines[:index]
        # Multiple possible bylines or generic title-like name lines are
        # ambiguous. Do not concatenate authors into the displayed paper name.
        if any(author_line(part) for part in prefix[1:]): return None
        title = clean(' '.join(prefix))
        if valid(title): return dict(title=title, method='First-page title block before author/affiliation', page=1, version=VERSION)
    return None

def filename_title(study):
    upload = study.get('paper_upload')
    if not isinstance(upload, dict): return False
    filename = upload.get('filename')
    return isinstance(filename, str) and clean(study.get('title')).casefold() in (clean(filename).casefold(), clean(filename[:-4]).casefold(), 'uploaded paper')

@lru_cache(maxsize=256)
def retained_title(study_id, original_sha256, epoch):
    # A short epoch also lets already-submitted papers pick up text packets
    # created after an initial queue read. No missing title is cached forever.
    artifacts = store.rows('artifacts', {'study_id': 'eq.' + identifier(study_id),
        'provenance->>kind': 'eq.derived-pdf-text', 'provenance->>original_sha256': 'eq.' + original_sha256,
        'select': 'id,study_id,sha256,byte_count,provenance', 'order': 'created_at.desc,id.desc', 'limit': 2})
    if not artifacts: return None
    record = artifacts[0]
    if not isinstance(record, dict): return None
    provenance = record.get('provenance', {})
    size = record.get('byte_count')
    if (not isinstance(provenance, dict) or record.get('study_id') != study_id or provenance.get('kind') != 'derived-pdf-text' or
        provenance.get('original_sha256') != original_sha256 or type(size) is not int or not 0 < size <= MAX_PACKET_BYTES): return None
    raw = store.artifact_bytes(study_id, identifier(record['id']), maximum=MAX_PACKET_BYTES)
    if len(raw) != size or digest(raw) != record['sha256']: raise store.StoreError('Title text packet identity differs.')
    packet = json.loads(raw)
    if not isinstance(packet, dict) or packet.get('original_sha256') != original_sha256: return None
    return extract_title(packet)

def display_title(study):
    if not filename_title(study): return study['title']
    upload = study['paper_upload']
    sha = upload.get('sha256')
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha): return 'Paper title unavailable'
    try:
        result = retained_title(study['id'], sha, int(time.monotonic() // 30))
        return result['title'] if result else 'Paper title unavailable'
    except (store.StoreError, ValueError, KeyError, TypeError):
        # Optional bibliographic display must not erase available queue status.
        return 'Paper title unavailable'
