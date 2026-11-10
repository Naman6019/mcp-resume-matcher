"""fs_tools -- sandboxed file-system tools for LLM tool calling (Part A).

Four tools, all usable as plain Python functions *and* exposed to an LLM via
``tool_schemas.py``:

    read_file(filepath, max_chars)            -> dict   PDF / DOCX / TXT text extraction
    list_files(directory, extension)          -> list   file metadata, optional extension filter
    write_file(filepath, content, overwrite)  -> dict   atomic write, creates parent dirs
    search_in_file(filepath, keyword, ...)    -> dict   case-insensitive search with context

Design rules
------------
* **Never raise from a dict-returning tool.**  Every failure comes back as
  ``{"success": False, "error": {"type": ..., "message": ...}}`` so an LLM can read the
  error and recover (retry with another path, pick another file, ...).
  ``list_files`` follows the brief's ``-> list`` signature, so it returns a plain list
  and raises :class:`FsToolError` on bad input instead; ``tool_schemas.execute_tool``
  converts that into the same error envelope for the LLM.
* **Sandboxed.**  Every path is resolved against a root directory (``FS_ROOT`` env var,
  default: the folder containing this file).  Absolute paths are accepted only if they
  stay inside the root, and ``..`` traversal / symlink escapes are rejected.  An LLM
  that can pick arbitrary paths must not be able to read ``C:\\Users\\...`` or ``~/.ssh``.
* **Bounded output.**  ``read_file`` truncates very long documents (flagged in the
  response) so a single tool result cannot blow the model's context window.
* **Safe writes.**  ``write_file`` refuses to overwrite unless asked, only writes
  plain-text formats, and writes atomically (temp file + ``os.replace``).
"""

from __future__ import annotations

import bisect
import functools
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

#: Formats ``read_file`` / ``search_in_file`` can extract text from.
READABLE_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".docx"})
#: Formats ``write_file`` will produce (plain text only -- we never fake a PDF/DOCX).
WRITABLE_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".json", ".csv", ".log"})

DEFAULT_MAX_CHARS = 20_000      # read_file truncation limit
MAX_WRITE_BYTES = 1_000_000     # refuse to write more than ~1 MB in one call
DEFAULT_CONTEXT_CHARS = 80      # characters of context either side of a search hit
DEFAULT_MAX_MATCHES = 20        # search_in_file returns at most this many matches

_DEFAULT_ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class FsToolError(Exception):
    """A predictable, user-facing failure (bad path, unsupported format, ...).

    ``error_type`` is a stable machine-readable slug included in the error envelope.
    """

    error_type = "error"

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        if error_type:
            self.error_type = error_type


class PathOutsideSandboxError(FsToolError):
    error_type = "outside_sandbox"


class FileMissingError(FsToolError):
    error_type = "not_found"


class UnsupportedFormatError(FsToolError):
    error_type = "unsupported_format"


class ExtractionError(FsToolError):
    error_type = "extraction_failed"


class InvalidArgumentError(FsToolError):
    error_type = "invalid_argument"


class FileExistsConflictError(FsToolError):
    error_type = "file_exists"


def _error_envelope(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, FsToolError):
        etype, message = exc.error_type, str(exc)
    elif isinstance(exc, PermissionError):
        etype, message = "permission_denied", f"Permission denied: {exc}"
    elif isinstance(exc, OSError):
        etype, message = "io_error", f"I/O error: {exc}"
    else:  # pragma: no cover - defensive; surfaced rather than swallowed silently
        etype, message = "internal_error", f"{type(exc).__name__}: {exc}"
    return {"success": False, "error": {"type": etype, "message": message}}


def safe_tool(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Convert any exception raised by a tool into the standard error envelope."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - this *is* the boundary
            return _error_envelope(exc)

    return wrapper


# --------------------------------------------------------------------------- #
# Sandbox / path helpers
# --------------------------------------------------------------------------- #

def get_root() -> Path:
    """Sandbox root, re-read on every call so tests / CLI flags can change it."""
    env = os.environ.get("FS_ROOT")
    return Path(env).expanduser().resolve() if env else _DEFAULT_ROOT


def _resolve(path: str) -> Path:
    """Resolve ``path`` inside the sandbox root or raise PathOutsideSandboxError."""
    if not isinstance(path, str) or not path.strip():
        raise InvalidArgumentError("Path must be a non-empty string.")
    root = get_root()
    raw = Path(path.strip()).expanduser()
    candidate = (raw if raw.is_absolute() else root / raw).resolve()  # collapses '..', follows symlinks
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PathOutsideSandboxError(
            f"Access denied: '{path}' is outside the allowed directory. "
            "Use a path relative to the project root, e.g. 'resumes/resume_john_doe.pdf'."
        ) from None
    return candidate


def _display(path: Path) -> str:
    """Path relative to the root, with forward slashes (stable for LLM consumption)."""
    try:
        return path.relative_to(get_root()).as_posix()
    except ValueError:  # pragma: no cover
        return str(path)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _normalise_extension(extension: str | None) -> str | None:
    if extension is None:
        return None
    if not isinstance(extension, str):
        raise InvalidArgumentError("extension must be a string such as '.pdf' or 'pdf'.")
    ext = extension.strip().lower()
    if not ext:
        return None
    return ext if ext.startswith(".") else f".{ext}"


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #

def _extract_txt(path: Path) -> tuple[str, dict[str, Any]]:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue  # only try UTF-16 when a BOM is present
            return data.decode(encoding), {"encoding": encoding}
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), {"encoding": "utf-8 (lossy)"}  # pragma: no cover


def _extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader  # lazy: keep import cost off the non-PDF paths
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")  # many "encrypted" PDFs use an empty user password
            except Exception:  # noqa: BLE001 - pypdf raises several types here
                ok = 0
            if not ok:
                raise ExtractionError("The PDF is password-protected and cannot be read.")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except ExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise ExtractionError(f"Could not parse PDF: {exc}") from exc
    return "\n\n".join(p for p in pages if p), {"page_count": len(pages)}


def _extract_docx(path: Path) -> tuple[str, dict[str, Any]]:
    import docx  # python-docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - python-docx raises BadZipFile, KeyError, ...
        raise ExtractionError(f"Could not parse DOCX: {exc}") from exc

    lines: list[str] = []
    n_tables = 0
    # Walk the body in document order so tables stay next to the paragraphs around them.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            n_tables += 1
            for row in Table(child, document).rows:
                seen: list[str] = []
                for cell in row.cells:  # merged cells repeat; de-duplicate adjacent repeats
                    text = cell.text.strip()
                    if text and (not seen or seen[-1] != text):
                        seen.append(text)
                if seen:
                    lines.append(" | ".join(seen))
    return "\n".join(lines), {"table_count": n_tables}


_EXTRACTORS: dict[str, Callable[[Path], tuple[str, dict[str, Any]]]] = {
    ".txt": _extract_txt,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}


def _normalise_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _load_text(path: Path) -> tuple[str, dict[str, Any]]:
    """Validate ``path`` and return (full normalised text, extractor-specific metadata)."""
    if not path.exists():
        raise FileMissingError(
            f"File not found: '{_display(path)}'. Use list_files to see what is available."
        )
    if not path.is_file():
        raise InvalidArgumentError(f"'{_display(path)}' is a directory, not a file.")
    ext = path.suffix.lower()
    if ext not in READABLE_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported file type '{ext or '(none)'}'. "
            f"Supported: {', '.join(sorted(READABLE_EXTENSIONS))}."
        )
    text, extra = _EXTRACTORS[ext](path)
    text = _normalise_text(text)
    if not text:
        hint = " (it may be a scanned image; OCR is not supported)" if ext == ".pdf" else ""
        raise ExtractionError(f"No extractable text found in '{_display(path)}'{hint}.")
    return text, extra


# --------------------------------------------------------------------------- #
# Tool 1: read_file
# --------------------------------------------------------------------------- #

@safe_tool
def read_file(filepath: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """Read a resume (PDF, DOCX or TXT) and return its text plus metadata.

    Args:
        filepath: Path inside the sandbox root (relative or absolute).
        max_chars: Cap on returned characters. Longer documents are truncated and
            ``metadata.truncated`` is set. Must be >= 1.

    Returns:
        ``{"success": True, "filepath": str, "content": str, "metadata": {...}}`` where
        metadata has ``filename, extension, format, size_bytes, modified, char_count,
        word_count, truncated`` (+ ``page_count`` for PDFs, ``table_count`` for DOCX,
        ``encoding`` for TXT); or ``{"success": False, "error": {...}}``.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise InvalidArgumentError("max_chars must be a positive integer.")
    path = _resolve(filepath)
    text, extra = _load_text(path)
    stat = path.stat()
    truncated = len(text) > max_chars
    metadata: dict[str, Any] = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "format": path.suffix.lower().lstrip("."),
        "size_bytes": stat.st_size,
        "modified": _iso(stat.st_mtime),
        "char_count": len(text),
        "word_count": len(text.split()),
        "truncated": truncated,
        **extra,
    }
    return {
        "success": True,
        "filepath": _display(path),
        "content": text[:max_chars] if truncated else text,
        "metadata": metadata,
    }


# --------------------------------------------------------------------------- #
# Tool 2: list_files
# --------------------------------------------------------------------------- #

def list_files(directory: str = ".", extension: str | None = None) -> list[dict[str, Any]]:
    """List the files directly inside ``directory`` (non-recursive).

    Args:
        directory: Directory inside the sandbox root.
        extension: Optional filter such as ``".pdf"`` or ``"pdf"`` (case-insensitive).

    Returns:
        A list of ``{"name", "path", "size_bytes", "modified", "extension"}`` dicts,
        sorted by name. Hidden files (dotfiles) and sub-directories are skipped.

    Raises:
        FsToolError: if the directory is missing, not a directory, or outside the sandbox.
            (This function returns a list per the assignment's signature, so errors are
            signalled by exception; ``execute_tool`` converts them for the LLM.)
    """
    folder = _resolve(directory)
    if not folder.exists():
        raise FileMissingError(f"Directory not found: '{_display(folder)}'.")
    if not folder.is_dir():
        raise InvalidArgumentError(f"'{_display(folder)}' is a file, not a directory.")
    wanted = _normalise_extension(extension)

    entries: list[dict[str, Any]] = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if item.name.startswith(".") or not item.is_file():
            continue
        if wanted and item.suffix.lower() != wanted:
            continue
        stat = item.stat()
        entries.append(
            {
                "name": item.name,
                "path": _display(item),
                "size_bytes": stat.st_size,
                "modified": _iso(stat.st_mtime),
                "extension": item.suffix.lower(),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# Tool 3: write_file
# --------------------------------------------------------------------------- #

@safe_tool
def write_file(filepath: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Write ``content`` to a plain-text file, creating parent directories as needed.

    Args:
        filepath: Destination inside the sandbox root. Extension must be one of
            ``.txt .md .json .csv .log`` (PDF/DOCX output is deliberately unsupported).
        content: Text to write (UTF-8, ``\\n`` newlines). At most 1 MB.
        overwrite: Replace an existing file. Defaults to ``False`` so an LLM cannot
            clobber data by accident; the error tells the caller how to proceed.

    Returns:
        ``{"success": True, "filepath", "bytes_written", "created_directories",
        "overwritten"}`` or ``{"success": False, "error": {...}}``.
    """
    if not isinstance(content, str):
        raise InvalidArgumentError("content must be a string.")
    path = _resolve(filepath)
    ext = path.suffix.lower()
    if ext not in WRITABLE_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Cannot write '{ext or '(none)'}' files. Allowed: {', '.join(sorted(WRITABLE_EXTENSIONS))}."
        )
    payload = content.encode("utf-8")
    if len(payload) > MAX_WRITE_BYTES:
        raise InvalidArgumentError(f"Content is {len(payload):,} bytes; the limit is {MAX_WRITE_BYTES:,}.")
    if path.is_dir():
        raise InvalidArgumentError(f"'{_display(path)}' is a directory.")
    existed = path.exists()
    if existed and not overwrite:
        raise FileExistsConflictError(
            f"'{_display(path)}' already exists. Pass overwrite=true to replace it, "
            "or choose a different filename."
        )

    missing = [p for p in reversed(path.parents) if not p.exists()]  # for reporting only
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: a crash mid-write can never leave a half-written destination file.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    return {
        "success": True,
        "filepath": _display(path),
        "bytes_written": len(payload),
        "created_directories": [_display(p) for p in missing],
        "overwritten": existed,
    }


# --------------------------------------------------------------------------- #
# Tool 4: search_in_file
# --------------------------------------------------------------------------- #

@safe_tool
def search_in_file(
    filepath: str,
    keyword: str,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    max_matches: int = DEFAULT_MAX_MATCHES,
    whole_word: bool = False,
) -> dict[str, Any]:
    """Case-insensitive keyword search with surrounding context.

    Searches the *full* extracted text (not the truncated ``read_file`` view).

    Args:
        filepath: PDF / DOCX / TXT inside the sandbox root.
        keyword: Literal text to find (not a regex). Matching ignores case.
        context_chars: Characters of context to include on each side of a hit.
        max_matches: Maximum matches returned; ``total_matches`` always has the true count.
        whole_word: Only match the keyword as a whole word (``"Java"`` won't match
            ``"JavaScript"``).

    Returns:
        ``{"success": True, "filepath", "keyword", "total_matches", "returned_matches",
        "truncated", "matches": [{"line", "offset", "match", "context"}]}``.
        Zero matches is a *successful* result with ``total_matches == 0``.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise InvalidArgumentError("keyword must be a non-empty string.")
    for name, value, minimum in (("context_chars", context_chars, 0), ("max_matches", max_matches, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise InvalidArgumentError(f"{name} must be an integer >= {minimum}.")

    path = _resolve(filepath)
    text, _ = _load_text(path)

    needle = re.escape(keyword.strip())
    pattern = re.compile(rf"(?<!\w){needle}(?!\w)" if whole_word else needle, re.IGNORECASE)

    # Precompute line starts so each match gets a 1-based line number in O(log n).
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    matches: list[dict[str, Any]] = []
    total = 0
    for m in pattern.finditer(text):
        total += 1
        if len(matches) >= max_matches:
            continue  # keep counting, stop collecting
        start, end = m.span()
        left, right = max(0, start - context_chars), min(len(text), end + context_chars)
        snippet = re.sub(r"\s+", " ", text[left:right]).strip()
        if left > 0:
            snippet = "..." + snippet
        if right < len(text):
            snippet += "..."
        matches.append(
            {
                "line": bisect.bisect_right(line_starts, start),
                "offset": start,
                "match": m.group(0),
                "context": snippet,
            }
        )

    return {
        "success": True,
        "filepath": _display(path),
        "keyword": keyword.strip(),
        "total_matches": total,
        "returned_matches": len(matches),
        "truncated": total > len(matches),
        "matches": matches,
    }
