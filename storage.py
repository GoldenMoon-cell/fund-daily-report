# -*- coding: utf-8 -*-
"""Durable JSON storage primitives for the fund diary.

This module owns filesystem safety only. It deliberately knows nothing about
accounts, holdings, trades, Qt, or the directory chosen by ``app.py``.
Callers always pass an explicit path so source and frozen builds keep the same
data-location contract.
"""

import json
import os
import shutil
import tempfile


def atomic_write_json(path, obj, **json_kwargs):
    """Write JSON through a same-directory temporary file and keep ``.bak``."""
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception:
        pass

    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, **json_kwargs)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def load_json_with_bak(path, default):
    """Load JSON, restoring a valid ``.bak`` when the primary file is broken."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default
    except Exception:
        pass

    try:
        with open(path + ".bak", "r", encoding="utf-8") as stream:
            data = json.load(stream)
        try:
            shutil.copy2(path + ".bak", path)
        except Exception:
            pass
        return data
    except Exception:
        return default
