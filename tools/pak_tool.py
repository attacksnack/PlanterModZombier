#!/usr/bin/env python3
"""
pak_tool.py - unpack and repack PopCap .pak archives (Plants vs. Zombies main.pak).

Usage:
    python pak_tool.py list    main.pak
    python pak_tool.py unpack  main.pak  extracted_folder
    python pak_tool.py repack  extracted_folder  main_new.pak [--force] [--no-text-fix]

repack automatically converts properties/LawnStrings.txt to single-byte
Windows-1252 while packing (the file on disk is left untouched), so you can
edit it as ordinary UTF-8 in any editor. Use --no-text-fix to disable this.

Format (PvZ PC): the whole file is XOR-encoded with 0xF7. Decoded layout:
    uint32  magic   0xBAC04AC0
    uint32  version 0
    records: uint8 flags (0x80 = end of list), then for normal entries:
             uint8 name_length, name (backslash separated),
             uint32 file_size, uint64 file_time (Windows FILETIME)
    then the raw file data of every entry, back to back, in record order.
"""
import argparse
import json
import os
import re
import struct
import sys
import time
from collections import Counter

MAGIC = 0xBAC04AC0
VERSION = 0
XOR_KEY = 0xF7
END_FLAG = 0x80
MANIFEST = "_manifest.json"
XOR_TABLE = bytes(b ^ XOR_KEY for b in range(256))

# Files whose text is converted to single-byte Windows-1252 while repacking.
# The game reads text one byte per character, so UTF-8 shows up garbled.
TEXT_FIXES = ("properties\\lawnstrings.txt",)
UTF8_SEQ = re.compile(rb"[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}")


def xor(data: bytes) -> bytes:
    return data.translate(XOR_TABLE)


def to_cp1252(data: bytes):
    """Convert UTF-8 sequences to Windows-1252 bytes; existing single bytes are kept.

    Works on UTF-8, Windows-1252, or a mix of both. Returns
    (new_bytes, converted_counter, failed_counter, bom_removed).
    """
    converted, failed = Counter(), Counter()
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]

    def fix(match):
        seq = match.group()
        try:
            text = seq.decode("utf-8")
            out = text.encode("cp1252")
        except UnicodeError:
            failed[seq] += 1
            return seq
        converted[text] += 1
        return out

    return UTF8_SEQ.sub(fix, data), converted, failed, bom


def read_pak(pak_path):
    """Return (entries, data, data_start) where entries = [(name, size, filetime)]."""
    with open(pak_path, "rb") as f:
        data = xor(f.read())
    if len(data) < 9:
        sys.exit("File is too small to be a PAK archive.")
    magic, version = struct.unpack_from("<II", data, 0)
    if magic != MAGIC:
        sys.exit(f"Not a PopCap PAK (magic 0x{magic:08X}, expected 0x{MAGIC:08X}). "
                 "This tool handles the uncompressed PvZ format only.")
    pos = 8
    entries = []
    while True:
        flags = data[pos]
        pos += 1
        if flags & END_FLAG:
            break
        name_len = data[pos]
        pos += 1
        name = data[pos:pos + name_len].decode("latin-1")
        pos += name_len
        size, ftime = struct.unpack_from("<IQ", data, pos)
        pos += 12
        entries.append((name, size, ftime))
    return entries, data, pos


def cmd_list(args):
    entries, _, _ = read_pak(args.pak)
    for name, size, _ in entries:
        print(f"{size:>10}  {name}")
    print(f"\n{len(entries)} files")


def cmd_unpack(args):
    entries, data, pos = read_pak(args.pak)
    out_root = os.path.abspath(args.folder)
    manifest = []
    for name, size, ftime in entries:
        rel = name.replace("\\", "/")
        target = os.path.abspath(os.path.join(out_root, rel))
        if os.path.commonpath([out_root, target]) != out_root:
            sys.exit(f"Refusing to write outside the output folder: {name}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data[pos:pos + size])
        pos += size
        manifest.append({"name": name, "time": ftime})
    with open(os.path.join(out_root, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print(f"Unpacked {len(entries)} files to {out_root}")


def cmd_repack(args):
    in_root = os.path.abspath(args.folder)
    if not os.path.isdir(in_root):
        sys.exit(f"Folder not found: {in_root}")
    if os.path.exists(args.pak) and not args.force:
        sys.exit(f"{args.pak} already exists. Choose another name or use --force.")

    # Collect every file currently in the folder (stored names use backslashes).
    on_disk = {}
    for dirpath, _, files in os.walk(in_root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, in_root)
            if rel == MANIFEST:
                continue
            on_disk[rel.replace("/", "\\").replace(os.sep, "\\")] = full

    # Keep the original order and timestamps where possible.
    ordered, times = [], {}
    manifest_path = os.path.join(in_root, MANIFEST)
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            for item in json.load(f):
                if item["name"] in on_disk:
                    ordered.append(item["name"])
                    times[item["name"]] = item["time"]
    ordered += sorted(n for n in on_disk if n not in set(ordered))

    now = int((time.time() + 11644473600) * 10_000_000)  # Unix -> FILETIME
    header = bytearray(struct.pack("<II", MAGIC, VERSION))
    body = bytearray()
    for name in ordered:
        raw_name = name.encode("latin-1")
        if len(raw_name) > 255:
            sys.exit(f"Name too long for the PAK format: {name}")
        with open(on_disk[name], "rb") as f:
            content = f.read()
        if name.lower() in TEXT_FIXES and not args.no_text_fix:
            content, converted, failed, bom = to_cp1252(content)
            print(f"  {name}: converted {sum(converted.values())} UTF-8 character(s) "
                  f"to Windows-1252" + (", removed BOM" if bom else ""))
            for seq, n in failed.items():
                print(f"  WARNING: {seq.decode('utf-8', 'replace')!r} x{n} does not exist "
                      f"in Windows-1252 and was left as-is; replace it in the text.")
        header += bytes([0, len(raw_name)]) + raw_name
        header += struct.pack("<IQ", len(content), times.get(name, now))
        body += content
    header += bytes([END_FLAG])

    with open(args.pak, "wb") as f:
        f.write(xor(bytes(header) + bytes(body)))
    print(f"Packed {len(ordered)} files into {args.pak}")


def main():
    p = argparse.ArgumentParser(description="PopCap PAK tool for Plants vs. Zombies")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list", help="list files inside a .pak")
    a.add_argument("pak")
    a.set_defaults(func=cmd_list)

    a = sub.add_parser("unpack", help="extract a .pak into a folder")
    a.add_argument("pak")
    a.add_argument("folder")
    a.set_defaults(func=cmd_unpack)

    a = sub.add_parser("repack", help="build a .pak from a folder")
    a.add_argument("folder")
    a.add_argument("pak")
    a.add_argument("--force", action="store_true", help="overwrite output if it exists")
    a.add_argument("--no-text-fix", action="store_true",
                   help="pack LawnStrings.txt exactly as it is, without converting to Windows-1252")
    a.set_defaults(func=cmd_repack)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
