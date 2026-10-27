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

VERSION = 'paper-display-title-v2'
AUTHORS_VERSION = 'paper-authors-v1'
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
    words = re.sub(r'[✉∗*†‡]', '', value).split()
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
        title = clean(re.sub(r'[∗*†‡]+\s*$', '', ' '.join(prefix)))
        if valid(title): return dict(title=title, method='First-page title block before author/affiliation', page=1, version=VERSION)
    return None

def filename_title(study):
    upload = study.get('paper_upload')
    if not isinstance(upload, dict): return False
    filename = upload.get('filename')
    return isinstance(filename, str) and clean(study.get('title')).casefold() in (clean(filename).casefold(), clean(filename[:-4]).casefold(), 'uploaded paper')

def author_names(value):
    """Validate an ordered bibliographic list, never owner/profile information."""
    if not isinstance(value, list) or not 1 <= len(value) <= 100: return None
    names = []
    for name in value:
        normalized = clean(name)
        if not 1 <= len(normalized) <= 200 or re.search(r'https?://|www\.|@|[<>]|\.pdf\b', normalized, re.I): return None
        names.append(normalized)
    return names

def saved_authors(value):
    if not isinstance(value, dict) or value.get('version') != AUTHORS_VERSION or value.get('page') != 1: return None
    return author_names(value.get('names'))

AFFILIATION = re.compile(r'\b(?:university|institute|department|dept|laborator(?:y|ies)|college|school|research|group|centre|center|science|engineering)\b', re.I)

def byline_names(raw):
    if not isinstance(raw, str) or len(raw) > 4000 or '@' in raw or AFFILIATION.search(raw): return None
    raw = re.sub(r'[∗*†‡✉\u00b9\u00b2\u00b3\u2070-\u2079]', '', raw)
    raw = re.sub(r'(?<=\w)\d+(?=\s*(?:[,;&]|$))', '', raw)
    parts = re.split(r'\s*(?:,|;|\band\b|&)\s*|\s{2,}', raw.strip())
    names = []
    particles = {'de', 'del', 'da', 'dos', 'van', 'von', 'der', 'la', 'le', 'di', 'du'}
    for part in parts:
        name = clean(part); words = name.split()
        if not 2 <= len(words) <= 8 or len(name) > 200 or not all(
            re.fullmatch(r"[^\W\d_][\w.'’\-]*", word, re.UNICODE) and not any(c.isdigit() or c == '_' for c in word) and
            (word[0].isupper() or word.casefold() in particles) for word in words): return None
        names.append(name)
    return author_names(names)

def extract_authors(packet):
    """Conservative first-page derivation; ambiguous/scanned layouts stay missing."""
    title = extract_title(packet)
    if not title: return None
    raw = packet['pages'][0]['text'][:6000]
    front = re.split(r'(?im)^\s*(?:abstract|summary|(?:1[. ]+)?introduction)\b', raw, maxsplit=1)[0]
    lines = [line.strip() for line in front.splitlines() if line.strip()]
    start = None
    for i in range(min(12, len(lines))):
        for end in range(i+1, min(i+5, len(lines)+1)):
            candidate = clean(re.sub(r'[∗*†‡]+\s*$', '', ' '.join(lines[i:end])))
            if candidate.casefold() == title['title'].casefold(): start = end; break
        if start is not None: break
    if start is None: return None
    block = lines[start:start+30]
    names = []
    for i, line in enumerate(block):
        candidate = byline_names(line)
        following = block[i+1:i+3]
        if candidate and any(AFFILIATION.search(next_line) for next_line in following):
            for name in candidate:
                if name not in names: names.append(name)
    method = 'First-page bylines adjacent to affiliation text'
    # PDF /Author can identify a document creator. Accept its names only when
    # every complete name also occurs in the paper's first-page author block.
    metadata = packet.get('document_metadata', {})
    supplied = byline_names(metadata.get('author')) if isinstance(metadata, dict) else None
    normalized = clean(' '.join(block)).casefold()
    if supplied and len(supplied) > len(names) and all(name.casefold() in normalized for name in supplied):
        names = supplied; method = 'PDF author metadata corroborated by first-page text'
    names = author_names(names)
    return dict(version=AUTHORS_VERSION, names=names, page=1, method=method) if names else None

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
    result = extract_title(packet) or {}
    authors = extract_authors(packet)
    if authors: result['authors'] = authors
    return result or None

def display_authors(study):
    upload = study.get('paper_upload')
    sha = upload.get('sha256') if isinstance(upload, dict) else None
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha): return None
    try:
        result = retained_title(study['id'], sha, int(time.monotonic() // 30))
        return saved_authors(result.get('authors')) if result else None
    except (store.StoreError, ValueError, KeyError, TypeError): return None

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
