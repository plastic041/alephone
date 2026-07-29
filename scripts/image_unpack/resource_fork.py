from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from PIL import Image


class FormatError(ValueError):
    pass


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _align(value: int, boundary: int = 128) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def _macbinary_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unpackbits(data: bytes, cursor: int, row_bytes: int, unit: int = 1) -> tuple[bytes, int]:
    count_bytes = 2 if row_bytes > 250 else 1
    packed_length = int.from_bytes(data[cursor : cursor + count_bytes], "big")
    cursor += count_bytes
    end = cursor + packed_length
    output = bytearray()
    while cursor < end:
        flag = struct.unpack_from(">b", data, cursor)[0]
        cursor += 1
        if flag >= 0:
            length = (flag + 1) * unit
            output += data[cursor : cursor + length]
            cursor += length
        elif flag != -128:
            value = data[cursor : cursor + unit]
            cursor += unit
            output += value * (-flag + 1)
    return bytes(output), end


def _decode_pict(payload: bytes) -> Image.Image:
    # Marathon Images PICTs place their first bitmap opcode after the v2 header.
    candidates = [(payload.find(bytes((0, opcode)), 40, 128), opcode) for opcode in (0x98, 0x9A)]
    candidates = [(position, opcode) for position, opcode in candidates if position >= 0]
    if not candidates:
        raise FormatError("PICT has no supported PackBitsRect/DirectBitsRect opcode")
    position, opcode = min(candidates)
    cursor = position + 2
    if opcode == 0x9A:
        cursor += 4  # pmBaseAddr
    pixmap = cursor
    row_bytes = _u16(payload, pixmap) & 0x3FFF
    top, left, bottom, right = struct.unpack_from(">4h", payload, pixmap + 2)
    width, height = right - left, bottom - top
    pack_type = _u16(payload, pixmap + 12)
    pixel_size = _u16(payload, pixmap + 28)
    cursor = pixmap + 46

    palette = None
    if opcode == 0x98:
        color_count = _u16(payload, cursor + 6) + 1
        palette = [(0, 0, 0)] * 256
        flags = _u16(payload, cursor + 4)
        cursor += 8
        for index in range(color_count):
            value, red, green, blue = struct.unpack_from(">4H", payload, cursor)
            cursor += 8
            palette[index if flags & 0x8000 else value & 0xFF] = (
                red >> 8,
                green >> 8,
                blue >> 8,
            )
    cursor += 18  # source rect, destination rect, transfer mode

    rgb = bytearray()
    for _ in range(height):
        if pack_type == 1 or row_bytes < 8:
            row = payload[cursor : cursor + row_bytes]
            cursor += row_bytes
        else:
            row, cursor = _unpackbits(payload, cursor, row_bytes, 2 if pixel_size == 16 else 1)
        if pixel_size == 8 and palette is not None:
            for value in row[:width]:
                rgb += bytes(palette[value])
        elif pixel_size == 16:
            for x in range(width):
                value = _u16(row, x * 2)
                rgb += bytes(
                    (
                        ((value >> 10) & 31) * 255 // 31,
                        ((value >> 5) & 31) * 255 // 31,
                        (value & 31) * 255 // 31,
                    )
                )
        elif pixel_size == 32:
            if pack_type in (0, 4):
                # QuickDraw stores compressed direct color as planar R, G, B.
                for x in range(width):
                    rgb += bytes((row[x], row[width + x], row[width * 2 + x]))
            else:
                for x in range(width):
                    rgb += row[x * 4 + 1 : x * 4 + 4]
        else:
            raise FormatError(f"unsupported PICT pixel depth: {pixel_size}")
    return Image.frombytes("RGB", (width, height), bytes(rgb))


def _export_pict_png(payload: bytes, destination: Path) -> None:
    _decode_pict(payload).save(destination, format="PNG")


def _png_to_pict(source: Path) -> bytes:
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        if not 1 <= width <= 32767 or not 1 <= height <= 32767:
            raise FormatError(f"PICT dimensions are out of range: {width}x{height}")
        row_bytes = width * 4
        if row_bytes > 0x7FFF:
            raise FormatError(f"PICT row is too wide: {width} pixels")
        pixels = bytearray()
        for red, green, blue in image.getdata():
            pixels += bytes((0, red, green, blue))

    rect = struct.pack(">4h", 0, 0, height, width)
    # PICT v2 header.
    output = bytearray(b"\0\0" + rect)
    output += b"\x00\x11\x02\xff\x0c\x00"
    output += b"\xff\xfe\0\0" + struct.pack(">I", 72 << 16) + struct.pack(">I", 72 << 16)
    output += rect + b"\0\0\0\0"
    # DirectBitsRect, baseAddr, then a 32-bit unpacked PixMap.
    output += b"\x00\x9a\0\0\0\0"
    pixmap = bytearray(struct.pack(">H", 0x8000 | row_bytes) + rect)
    pixmap += struct.pack(">HHI", 0, 1, 0)
    pixmap += struct.pack(">II", 72 << 16, 72 << 16)
    pixmap += struct.pack(">HHHH", 0x10, 32, 3, 8)
    pixmap += b"\0" * 12
    output += pixmap
    output += rect + rect + b"\0\0"
    output += pixels
    output += b"\x00\xff"
    struct.pack_into(">H", output, 0, len(output) & 0xFFFF)
    return bytes(output)


def split_container(blob: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(blob) >= 128 and blob[0] == 0 and 1 <= blob[1] <= 63:
        data_length = _u32(blob, 83)
        resource_length = _u32(blob, 87)
        resource_start = 128 + _align(data_length)
        if resource_start + resource_length <= len(blob):
            return blob[resource_start : resource_start + resource_length], {
                "kind": "macbinary",
                "header": base64.b64encode(blob[:128]).decode("ascii"),
                "data_file": "data_fork.bin",
                "data": blob[128 : 128 + data_length],
            }
    return blob, {"kind": "raw"}


def parse_resource_fork(fork: bytes) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    if len(fork) < 16:
        raise FormatError("resource fork is shorter than its 16-byte header")
    data_offset, map_offset, data_length, map_length = struct.unpack_from(">4I", fork)
    if (
        data_offset + data_length > len(fork)
        or map_offset + map_length > len(fork)
        or map_length < 28
    ):
        raise FormatError("invalid resource fork offsets")

    type_list = map_offset + _u16(fork, map_offset + 24)
    name_list = map_offset + _u16(fork, map_offset + 26)
    if type_list + 2 > len(fork) or name_list > map_offset + map_length:
        raise FormatError("invalid resource map")

    type_count = _u16(fork, type_list) + 1
    resources: list[tuple[dict[str, Any], bytes]] = []
    for type_index in range(type_count):
        entry = type_list + 2 + type_index * 8
        if entry + 8 > len(fork):
            raise FormatError("truncated resource type list")
        type_bytes = fork[entry : entry + 4]
        count = _u16(fork, entry + 4) + 1
        references = type_list + _u16(fork, entry + 6)
        for resource_index in range(count):
            ref = references + resource_index * 12
            if ref + 12 > len(fork):
                raise FormatError("truncated resource reference list")
            resource_id = _s16(fork, ref)
            name_offset = _s16(fork, ref + 2)
            attributes = fork[ref + 4]
            relative_data = int.from_bytes(fork[ref + 5 : ref + 8], "big")
            payload_at = data_offset + relative_data
            if payload_at + 4 > len(fork):
                raise FormatError("resource data offset is outside the file")
            payload_length = _u32(fork, payload_at)
            payload = fork[payload_at + 4 : payload_at + 4 + payload_length]
            if len(payload) != payload_length:
                raise FormatError("truncated resource payload")

            name = None
            if name_offset != -1:
                name_at = name_list + name_offset
                if name_at >= map_offset + map_length:
                    raise FormatError("resource name offset is outside the map")
                length = fork[name_at]
                raw_name = fork[name_at + 1 : name_at + 1 + length]
                name = raw_name.decode("mac_roman")
            item = {
                "type_hex": type_bytes.hex(),
                "type": type_bytes.decode("mac_roman"),
                "id": resource_id,
                "name": name,
                "attributes": attributes,
            }
            resources.append((item, payload))

    map_attributes = _u16(fork, map_offset + 22)
    return {
        "format": "classic-mac-resource-fork",
        "version": 1,
        "map_attributes": map_attributes,
    }, resources


def unpack(source: Path, destination: Path) -> int:
    source_blob = source.read_bytes()
    fork, container = split_container(source_blob)
    metadata, resources = parse_resource_fork(fork)
    destination.mkdir(parents=True, exist_ok=True)
    editable_dir = destination / "editable"
    editable_dir.mkdir(exist_ok=True)
    original_file = ".original"
    (destination / original_file).write_bytes(source_blob)

    if container["kind"] == "macbinary":
        container.pop("data")
        container.pop("data_file")

    seen: dict[tuple[str, int], int] = {}
    entries = []
    for item, payload in resources:
        key = (item["type_hex"], item["id"])
        duplicate = seen.get(key, 0)
        seen[key] = duplicate + 1
        suffix = f"_{duplicate}" if duplicate else ""
        entry = {**item}
        if item["type_hex"] == "50494354":
            png_filename = f"PICT_{item['id']}{suffix}.png"
            png_path = editable_dir / png_filename
            _export_pict_png(payload, png_path)
            entry["editable_file"] = f"editable/{png_filename}"
            entry["editable_sha256"] = _sha256(png_path)
        entries.append(entry)

    metadata["source_name"] = source.name
    metadata["original_file"] = original_file
    metadata["container"] = container
    metadata["resources"] = entries
    (destination / "manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(entries)


def build_resource_fork(metadata: dict[str, Any], root: Path) -> bytes:
    entries = metadata.get("resources")
    if not isinstance(entries, list):
        raise FormatError("manifest has no resources list")

    original_payloads: list[bytes] = []
    if metadata.get("original_file"):
        original_blob = (root / metadata["original_file"]).read_bytes()
        original_fork, _ = split_container(original_blob)
        _, parsed = parse_resource_fork(original_fork)
        original_payloads = [payload for _, payload in parsed]
        if len(original_payloads) != len(entries):
            raise FormatError("original file resource count differs from manifest")

    data_area = bytearray()
    offsets = []
    for index, entry in enumerate(entries):
        if original_payloads:
            payload = original_payloads[index]
        elif entry.get("file"):  # Compatibility with older unpack directories.
            payload = (root / entry["file"]).read_bytes()
        else:
            raise FormatError(f"no original payload for resource {entry.get('type')} {entry.get('id')}")
        editable_file = entry.get("editable_file")
        if editable_file:
            editable_path = root / editable_file
            if not editable_path.exists():
                raise FormatError(f"editable image is missing: {editable_file}")
            if _sha256(editable_path) != entry.get("editable_sha256"):
                payload = _png_to_pict(editable_path)
        if len(data_area) > 0xFFFFFF:
            raise FormatError("resource data offset exceeds the 24-bit format limit")
        offsets.append(len(data_area))
        data_area += struct.pack(">I", len(payload)) + payload

    grouped: dict[bytes, list[tuple[dict[str, Any], int]]] = {}
    type_order: list[bytes] = []
    for entry, offset in zip(entries, offsets):
        type_bytes = bytes.fromhex(entry["type_hex"])
        if len(type_bytes) != 4:
            raise FormatError(f"resource type must contain four bytes: {entry['type_hex']}")
        if type_bytes not in grouped:
            grouped[type_bytes] = []
            type_order.append(type_bytes)
        grouped[type_bytes].append((entry, offset))

    names = bytearray()
    name_offsets: dict[int, int] = {}
    for entry in entries:
        if entry.get("name") is not None:
            raw = str(entry["name"]).encode("mac_roman")
            if len(raw) > 255:
                raise FormatError("resource name exceeds 255 bytes")
            name_offsets[id(entry)] = len(names)
            names += bytes([len(raw)]) + raw

    type_list = bytearray(struct.pack(">H", len(type_order) - 1))
    reference_start = 2 + len(type_order) * 8
    references = bytearray()
    for type_bytes in type_order:
        group = grouped[type_bytes]
        type_list += type_bytes + struct.pack(">HH", len(group) - 1, reference_start + len(references))
        for entry, offset in group:
            resource_id = int(entry["id"])
            if not -32768 <= resource_id <= 32767:
                raise FormatError(f"resource ID is outside signed 16-bit range: {resource_id}")
            name_offset = name_offsets.get(id(entry), -1)
            attributes = int(entry.get("attributes", 0))
            if not 0 <= attributes <= 255 or offset > 0xFFFFFF:
                raise FormatError("invalid resource attributes or data offset")
            references += struct.pack(">hhB", resource_id, name_offset, attributes)
            references += offset.to_bytes(3, "big") + b"\0\0\0\0"
    type_list += references

    data_offset = 256
    map_offset = data_offset + len(data_area)
    type_list_offset = 28
    name_list_offset = type_list_offset + len(type_list)
    map_length = name_list_offset + len(names)
    header = struct.pack(">4I", data_offset, map_offset, len(data_area), map_length)
    resource_map = bytearray(header)
    resource_map += b"\0\0\0\0"  # next resource map handle
    resource_map += b"\0\0"  # file reference number
    resource_map += struct.pack(">H", int(metadata.get("map_attributes", 0)))
    resource_map += struct.pack(">HH", type_list_offset, name_list_offset)
    resource_map += type_list + names
    return header + bytes(data_offset - 16) + data_area + resource_map


def pack(source: Path, destination: Path) -> int:
    metadata = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    fork = build_resource_fork(metadata, source)
    container = metadata.get("container", {"kind": "raw"})
    if container.get("kind") == "macbinary":
        if metadata.get("original_file"):
            original = (source / metadata["original_file"]).read_bytes()
            _, saved_container = split_container(original)
            header = bytearray(base64.b64decode(saved_container["header"]))
            data = saved_container["data"]
        else:
            header = bytearray(base64.b64decode(container["header"]))
            data = (source / container["data_file"]).read_bytes()
        if len(header) != 128:
            raise FormatError("saved MacBinary header is not 128 bytes")
        struct.pack_into(">I", header, 83, len(data))
        struct.pack_into(">I", header, 87, len(fork))
        header[124:126] = b"\0\0"
        struct.pack_into(">H", header, 124, _macbinary_crc(header[:124]))
        result = bytes(header) + data + bytes(_align(len(data)) - len(data))
        result += fork + bytes(_align(len(fork)) - len(fork))
    elif container.get("kind") == "raw":
        result = fork
    else:
        raise FormatError(f"unsupported container kind: {container.get('kind')!r}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    return len(metadata["resources"])
