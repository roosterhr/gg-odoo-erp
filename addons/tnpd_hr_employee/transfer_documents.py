# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
Supporting documents on a transfer request: which reasons need them, and what
a file must look like to be accepted.

No Odoo imports, so the checks can be tested on their own.

    reasons_need_documents(['illness_self'])                    # True
    reasons_need_documents(['single_parent'])                   # False
    reasons_need_documents(['single_parent', 'illness_self'])   # True: one of them needs it
    clean = validate_files([('cert.pdf', data)])
"""

import os
import re

MAX_FILES = 3
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file

# A request needs at least one document, unless every reason chosen is one of these.
DOCUMENT_EXEMPT_REASONS = ('single_parent',)

# extension -> (mimetype, first bytes of a genuine file of that type)
_TYPES = {
    'pdf':  ('application/pdf', b'%PDF-'),
    'jpg':  ('image/jpeg',      b'\xff\xd8\xff'),
    'jpeg': ('image/jpeg',      b'\xff\xd8\xff'),
    'png':  ('image/png',       b'\x89PNG\r\n\x1a\n'),
}

ALLOWED_MIMETYPES = frozenset(m for m, _ in _TYPES.values())


class UploadError(ValueError):
    """A file or a set of files that cannot be accepted. The message is safe to show."""


def reasons_need_documents(codes):
    """
    True when a request with these reasons must carry at least one document.

    A request may have several reasons. It needs a document unless every one of them
    is exempt, so "Single parent" together with any other reason still needs one.
    With no reason there is nothing to judge here: the reason itself is checked first.
    """
    return any(code not in DOCUMENT_EXEMPT_REASONS for code in (codes or []))


def clean_filename(name):
    """The file name only (no folders), without odd characters, at most 100 long."""
    base = os.path.basename((name or '').replace('\\', '/')).strip()
    base = re.sub(r'[^\w.\- ()]+', '_', base, flags=re.UNICODE).strip(' .')
    if len(base) > 100:
        stem, dot, ext = base.rpartition('.')
        base = (stem[:99 - len(ext)] + '.' + ext) if dot and len(ext) < 10 else base[:100]
    return base


def check_file(filename, data):
    """
    Accept one file or raise ``UploadError``.

    The type is judged by the file's own first bytes as well as its extension, so
    a renamed program or web page is refused.
    Returns (clean_name, mimetype).
    """
    name = clean_filename(filename)
    if not name:
        raise UploadError('A file has no name.')
    ext = name.rpartition('.')[2].lower() if '.' in name else ''
    if ext not in _TYPES:
        raise UploadError('"%s": only PDF, JPG or PNG files are allowed.' % name)
    if not data:
        raise UploadError('"%s" is empty.' % name)
    if len(data) > MAX_BYTES:
        raise UploadError('"%s" is too large. The limit is %d MB.' % (name, MAX_BYTES // (1024 * 1024)))
    mimetype, magic = _TYPES[ext]
    if not data.startswith(magic):
        raise UploadError('"%s" is not a valid %s file.' % (name, ext.upper()))
    return name, mimetype


def validate_files(files, already=0):
    """
    Check a batch of (filename, bytes) pairs.

    ``already`` is how many files the request holds, so the total cannot go over
    ``MAX_FILES``. Returns [(clean_name, mimetype, bytes), ...] or raises
    ``UploadError`` naming the first problem.
    """
    files = list(files or [])
    if already + len(files) > MAX_FILES:
        raise UploadError('You can attach up to %d files to a request.' % MAX_FILES)
    out = []
    for filename, data in files:
        name, mimetype = check_file(filename, data)
        out.append((name, mimetype, data))
    return out
