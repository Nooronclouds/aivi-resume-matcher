#!/usr/bin/env python3
"""CLI: score a resume against a job description.

stdout  -> exactly one JSON object (result, or {"error": {...}})
stderr  -> logs / warnings
exit    -> 0 ok | 2 bad input | 3 LLM unavailable | 4 invalid model output | 5 config/auth | 1 other
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from matcher.config import Settings
from matcher.llm_client import LLMAuthError, LLMError, LLMUnavailableError
from matcher.pipeline import OutputValidationError, evaluate
from matcher.preprocess import InputError


def read_input(path_str: str) -> str:
    if path_str == "-":
        return sys.stdin.read()
    path = Path(path_str)
    if not path.is_file():
        raise InputError("FILE_NOT_FOUND", f"no such file: {path}")
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise InputError("PDF_UNSUPPORTED", "install pypdf to read PDF input") from exc
        try:
            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # corrupt / encrypted PDFs
            raise InputError("PDF_UNREADABLE", f"could not read PDF: {exc}") from exc
    return path.read_text(encoding="utf-8", errors="replace")


def emit(obj: dict, pretty: bool) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", required=True, help="resume file (.txt/.md/.pdf) or '-' for stdin")
    ap.add_argument("--jd", required=True, help="job description file or '-' for stdin")
    ap.add_argument("--pretty", action="store_true", help="indent JSON output")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging to stderr")
    args = ap.parse_args(argv)

    try:  # optional .env support
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if args.resume == "-" and args.jd == "-":
        ap.error("only one of --resume/--jd can read from stdin")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def fail(code: str, msg: str, exit_code: int) -> int:
        emit({"error": {"code": code, "message": msg}}, args.pretty)
        return exit_code

    try:
        result = evaluate(read_input(args.resume), read_input(args.jd), Settings())
    except InputError as e:
        return fail(e.code, str(e), 2)
    except LLMAuthError as e:
        return fail(e.code, str(e), 5)
    except LLMUnavailableError as e:
        return fail(e.code, str(e), 3)
    except OutputValidationError as e:
        return fail(e.code, str(e), 4)
    except LLMError as e:
        return fail(e.code, str(e), 1)
    except Exception as e:  # last resort: never print a traceback to stdout
        logging.exception("unexpected failure")
        return fail("INTERNAL_ERROR", f"{type(e).__name__}: {e}", 1)

    emit(result.model_dump(), args.pretty)
    return 0


if __name__ == "__main__":
    sys.exit(main())
