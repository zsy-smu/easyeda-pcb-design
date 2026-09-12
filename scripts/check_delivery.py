#!/usr/bin/env python3
"""Snapshot/verify delivery bytes. Python 3.10+, standard library, no EDA calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import tempfile

MANIFEST_SCHEMA = "easyeda-delivery-manifest/v1"
REPORT_SCHEMA = "easyeda-delivery-verification/v1"


class InvalidInput(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidInput(message)


def no_reparse(info, label):
    # is_symlink alone misses Windows directory junctions and other reparse tags.
    require(not stat.S_ISLNK(info.st_mode)
            and not (getattr(info, "st_file_attributes", 0)
                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
            f"Symlink/reparse point is not supported: {label}")


def checked_path(value, *, must_exist=False):
    """Inspect lexical ancestors before resolving, so a junction is never hidden."""
    path = Path(os.path.abspath(os.fspath(value)))
    for part in reversed((path, *path.parents)):
        try:
            no_reparse(part.lstat(), part)
        except FileNotFoundError:
            if must_exist:
                raise InvalidInput(f"Path does not exist: {part}")
    require(path.resolve(strict=False) == path, f"Path alias is unsupported: {path}")
    return path


def relative_entry(value):
    require(isinstance(value, str) and bool(value), "File path must be a nonempty string")
    require(not any(ord(c) < 32 for c in value) and "\\" not in value
            and ":" not in value, f"Nonportable relative path: {value!r}")
    p = PurePosixPath(value)
    require(not p.is_absolute() and not PureWindowsPath(value).drive,
            f"Absolute file entry is forbidden: {value!r}")
    parts = value.split("/")
    require(all(s not in ("", ".", "..") and not s.endswith((".", " "))
                for s in parts), f"Noncanonical relative path: {value!r}")
    require(all(not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", s)
                for s in parts), f"Reserved file name: {value!r}")
    return value


def inside(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def alias(a, b):
    return (os.path.normcase(str(a)) == os.path.normcase(str(b))
            or (a.exists() and b.exists() and os.path.samefile(a, b)))


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def hash_file(path):
    before = path.lstat()
    no_reparse(before, path)
    require(stat.S_ISREG(before.st_mode), f"Not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        require(fingerprint(os.fstat(stream.fileno())) == fingerprint(before),
                f"File changed before reading: {path}")
        digest = hashlib.sha256()
        size = 0
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    no_reparse(current, path)
    require(fingerprint(before) == fingerprint(after) == fingerprint(current)
            and size == before.st_size, f"File changed while reading: {path}")
    return {"size": size, "sha256": digest.hexdigest()}


def inventory(root, ignored):
    result = []
    seen = set()
    ignored_keys = {os.path.normcase(p) for p in ignored}

    def visit(directory):
        checked_path(directory, must_exist=True)
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda e: e.name)
        for entry in entries:
            path = directory / entry.name
            rel = relative_entry(path.relative_to(root).as_posix())
            info = path.lstat()
            no_reparse(info, path)
            require(rel.casefold() not in seen, f"Case-colliding paths: {rel}")
            seen.add(rel.casefold())
            if stat.S_ISDIR(info.st_mode):
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                if os.path.normcase(rel) not in ignored_keys:
                    result.append({"path": rel, **hash_file(path)})
            else:
                raise InvalidInput(f"Unsupported filesystem entry: {rel}")

    visit(root)
    return sorted(result, key=lambda f: f["path"])


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, f"Duplicate JSON key: {key}")
        obj[key] = value
    return obj


def invalid_constant(value):
    raise InvalidInput(f"Invalid JSON number: {value}")


def read_json(path):
    require(stat.S_ISREG(path.lstat().st_mode), f"Not a JSON file: {path}")
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def validate_manifest(data):
    require(isinstance(data, dict) and set(data) == {"schema", "root", "manifestPath", "files"},
            "Invalid manifest fields")
    require(data["schema"] == MANIFEST_SCHEMA, "Unsupported manifest schema")
    root = data["root"]
    require(isinstance(root, str) and (Path(root).is_absolute() or PureWindowsPath(root).is_absolute()),
            "Manifest root must be an absolute directory path")
    excluded = data["manifestPath"]
    if excluded is not None:
        relative_entry(excluded)
    require(isinstance(data["files"], list), "Manifest files must be an array")
    seen = set()
    for item in data["files"]:
        require(isinstance(item, dict) and set(item) == {"path", "size", "sha256"}, "Invalid file entry fields")
        name = relative_entry(item["path"])
        require(name.casefold() not in seen, f"Duplicate/case-colliding entry: {name}")
        seen.add(name.casefold())
        require(type(item["size"]) is int and item["size"] >= 0, f"Invalid size: {name}")
        require(isinstance(item["sha256"], str) and re.fullmatch("[0-9a-f]{64}", item["sha256"]), f"Invalid SHA256: {name}")
        require(excluded is None or name.casefold() != excluded.casefold(), "Manifest cannot inventory itself")
    all_file_paths = seen | ({excluded.casefold()} if excluded is not None else set())
    for name in all_file_paths:
        require(not any(p.as_posix() in all_file_paths for p in PurePosixPath(name).parents if p.as_posix() != "."),
                f"A file entry is also a parent directory: {name}")
    return data


def output_guard(output, source=None, allowed_schema=None):
    if source is not None:
        require(not alias(output, source), "Output must not overwrite or alias the source manifest")
    if output.exists():
        require(output.is_file(), "Output is not a regular file")
        previous = read_json(output)
        require(isinstance(previous, dict) and previous.get("schema") == allowed_schema,
                "Refusing to overwrite an unrelated output file")


def write_json(output, data):
    output.parent.mkdir(parents=True, exist_ok=True)
    checked_path(output)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=".delivery-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def snapshot(directory, out):
    root = checked_path(directory, must_exist=True)
    require(root.is_dir(), "Snapshot source must be a directory")
    output = checked_path(out)
    require(not alias(root, output), "Manifest output must not replace the source directory")
    output_guard(output, allowed_schema=MANIFEST_SCHEMA)
    rel = inside(output, root)
    if rel is not None:
        relative_entry(rel)
    files = inventory(root, {rel} if rel is not None else set())
    data = {"schema": MANIFEST_SCHEMA, "root": str(root), "manifestPath": rel, "files": files}
    validate_manifest(data)
    write_json(output, data)
    return data


def verify(manifest, out, directory=None):
    source = checked_path(manifest, must_exist=True)
    output = checked_path(out)
    output_guard(output, source=source, allowed_schema=REPORT_SCHEMA)
    data = validate_manifest(read_json(source))
    root = checked_path(directory if directory is not None else data["root"], must_exist=True)
    require(root.is_dir(), "Verification root must be a directory")
    require(not alias(root, output), "Report output must not replace the source directory")
    expected = {f["path"]: f for f in data["files"]}
    ignored = set()
    if data["manifestPath"] is not None:
        ignored.add(data["manifestPath"])
    for p in (source, output):
        rel = inside(p, root)
        if rel is not None:
            relative_entry(rel)
            require(rel.casefold() not in {n.casefold() for n in expected},
                    "Manifest/report path would hide a tracked source artifact")
            ignored.add(rel)
    for name in expected:
        tracked = checked_path(root / name)
        require(not alias(output, tracked), "Report output aliases a tracked source artifact")
    actual = {f["path"]: f for f in inventory(root, ignored)}
    missing = sorted(expected.keys() - actual.keys())
    extra = [actual[n] for n in sorted(actual.keys() - expected.keys())]
    changed = [{"path": n, "expected": expected[n], "actual": actual[n]}
               for n in sorted(expected.keys() & actual.keys()) if expected[n] != actual[n]]
    report = {"schema": REPORT_SCHEMA, "scope": "File bytes only; no electrical or manufacturing correctness claim.",
              "passed": not (missing or extra or changed), "manifest": str(source), "root": str(root),
              "ignoredPaths": sorted(ignored), "expectedFileCount": len(expected), "actualFileCount": len(actual),
              "missing": missing, "extra": extra, "changed": changed}
    write_json(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="Freeze a sorted size/SHA256 inventory")
    snap.add_argument("directory")
    snap.add_argument("--out", required=True)
    check = commands.add_parser("verify", help="Detect changed, missing and extra files")
    check.add_argument("manifest")
    check.add_argument("--root", dest="directory")
    check.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.directory, args.out)
            print(f"Snapshot: {len(result['files'])} files; {args.out}")
            return 0
        result = verify(args.manifest, args.out, args.directory)
        print(f"Verification: {'PASS' if result['passed'] else 'MISMATCH'}; "
              f"changed={len(result['changed'])}, missing={len(result['missing'])}, extra={len(result['extra'])}; {args.out}")
        return 0 if result["passed"] else 1
    except (ValueError, OSError, UnicodeError, TypeError, OverflowError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
