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
import zipfile


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


def atomic_write_bytes(path, payload):
    """Atomically replace a file with raw bytes without changing formatting."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def atomic_write_json_group(entries):
    """Atomically commit several JSON files as one logical transaction.

    ``entries`` maps paths to ``(data, json_kwargs)``. Each individual replace
    is atomic; if any replace fails, every primary and backup file is restored
    byte-for-byte to its pre-transaction state.
    """
    originals = {}
    for path in entries:
        for target in (path, path + ".bak"):
            originals[target] = open(target, "rb").read() if os.path.exists(target) else None
    try:
        for path, (data, kwargs) in entries.items():
            atomic_write_json(path, data, **(kwargs or {}))
    except Exception:
        rollback_error = None
        for path, payload in originals.items():
            try:
                if payload is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    atomic_write_bytes(path, payload)
            except Exception as exc:
                rollback_error = rollback_error or exc
        if rollback_error:
            raise RuntimeError(f"保存失败，自动回滚也未完全成功：{rollback_error}")
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


def inspect_json_backup(zip_path, validators, manifest_name, supported_schema):
    """Parse and validate a backup without writing anything to disk."""
    result = {"ok": False, "files": {}, "manifest": None, "warnings": [], "errors": []}
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > 50 or sum(info.file_size for info in infos) > 100 * 1024 * 1024:
                result["errors"].append("压缩包条目过多或展开后超过 100MB 安全上限")
                return result
            broken = archive.testzip()
            if broken:
                result["errors"].append(f"压缩包校验失败：{broken}")
                return result
            names = [info.filename for info in infos if not info.is_dir()]
            if len(names) != len(set(names)):
                result["errors"].append("压缩包内存在重名文件")
                return result
            for name in names:
                if name != os.path.basename(name) or "/" in name or "\\" in name:
                    result["errors"].append(f"不允许的路径：{name}")
                    return result
            allowed = set(validators) | {manifest_name}
            unknown = sorted(set(names) - allowed)
            if unknown:
                result["warnings"].append("已忽略非数据文件：" + "、".join(unknown))

            if manifest_name in names:
                try:
                    manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
                except Exception as exc:
                    result["errors"].append(f"数据版本文件无法解析：{exc}")
                    return result
                if not isinstance(manifest, dict):
                    result["errors"].append("数据版本文件必须是对象")
                    return result
                version = manifest.get("schema_version", 0)
                if not isinstance(version, int) or version < 0:
                    result["errors"].append("数据版本号无效")
                    return result
                if version > supported_schema:
                    result["errors"].append(
                        f"备份格式版本 {version} 高于当前程序支持的 {supported_schema}，请升级程序后恢复"
                    )
                    return result
                file_versions = manifest.get("files", {})
                if not isinstance(file_versions, dict):
                    result["errors"].append("逐文件版本清单无效")
                    return result
                for name, file_version in file_versions.items():
                    if name not in validators or not isinstance(file_version, int) or file_version < 0:
                        result["errors"].append(f"{name} 的文件版本无效")
                        return result
                    if file_version > supported_schema:
                        result["errors"].append(f"{name} 的格式版本 {file_version} 高于当前支持的 {supported_schema}")
                        return result
                result["manifest"] = manifest
            else:
                result["warnings"].append("旧版备份：未包含数据版本文件，将按旧格式兼容恢复")

            for name, validator in validators.items():
                if name not in names:
                    continue
                info = archive.getinfo(name)
                if info.file_size > 50 * 1024 * 1024:
                    result["errors"].append(f"{name} 超过 50MB 安全上限")
                    continue
                try:
                    data = json.loads(archive.read(name).decode("utf-8-sig"))
                    error = validator(data)
                    if error:
                        result["errors"].append(f"{name}：{error}")
                    else:
                        result["files"][name] = data
                except Exception as exc:
                    result["errors"].append(f"{name} 无法解析：{exc}")
            if not result["files"]:
                result["errors"].append("备份中没有可恢复的数据文件")
    except (OSError, zipfile.BadZipFile) as exc:
        result["errors"].append(f"无法打开备份：{exc}")
    result["ok"] = not result["errors"]
    return result


def create_json_backup(zip_path, source_paths, manifest_name, manifest):
    """Create a zip through a temporary sibling file, then replace atomically."""
    target = os.path.abspath(zip_path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(target) + ".", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in source_paths.items():
                if name != manifest_name and os.path.exists(path):
                    archive.write(path, name)
            archive.writestr(
                manifest_name,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        os.replace(tmp, target)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise
    return target


def restore_json_backup(inspected, target_paths, snapshot_path, manifest_name, current_manifest, migrations=None):
    """Snapshot current files, atomically restore, and roll back every target on failure."""
    if not inspected.get("ok"):
        raise ValueError("备份尚未通过校验")
    selected = {name: data for name, data in inspected["files"].items() if name in target_paths}
    if not selected:
        raise ValueError("没有与当前程序匹配的数据文件")

    originals = {}
    for name in selected:
        path = target_paths[name]
        originals[path] = open(path, "rb").read() if os.path.exists(path) else None
        originals[path + ".bak"] = open(path + ".bak", "rb").read() if os.path.exists(path + ".bak") else None

    existing = {name: path for name, path in target_paths.items() if os.path.exists(path)}
    create_json_backup(snapshot_path, existing, manifest_name, current_manifest)

    source_version = int((inspected.get("manifest") or {}).get("schema_version", 0))
    migrations = migrations or {}
    try:
        for name, data in selected.items():
            migrate = migrations.get(name)
            if migrate:
                data = migrate(data, source_version)
            atomic_write_json(target_paths[name], data, indent=2)
    except Exception:
        rollback_error = None
        for path, payload in originals.items():
            try:
                if payload is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    atomic_write_bytes(path, payload)
            except Exception as exc:
                rollback_error = rollback_error or exc
        if rollback_error:
            raise RuntimeError(f"恢复失败，自动回滚也未完全成功：{rollback_error}")
        raise
    return {"restored": sorted(selected), "snapshot": snapshot_path}
