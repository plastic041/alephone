from __future__ import annotations

import hashlib
import json
import struct
import tempfile
from pathlib import Path

from PIL import Image

from resource_fork import (
    FormatError,
    pack as pack_resources,
    parse_resource_fork,
    split_container,
    unpack as unpack_resources,
)


COLLECTION = 10
RESOURCE_TYPE = b".256"
RESOURCE_ID = 128 + COLLECTION


def is_m1_shapes(source: Path) -> bool:
    try:
        fork, _ = split_container(source.read_bytes())
        _, resources = parse_resource_fork(fork)
    except (OSError, FormatError):
        return False
    return any(
        bytes.fromhex(item["type_hex"]) == RESOURCE_TYPE and item["id"] == RESOURCE_ID
        for item, _ in resources
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collection_payload(blob: bytes) -> bytes:
    fork, _ = split_container(blob)
    _, resources = parse_resource_fork(fork)
    for item, payload in resources:
        if bytes.fromhex(item["type_hex"]) == RESOURCE_TYPE and item["id"] == RESOURCE_ID:
            return payload
    raise FormatError("Marathon 1 menu collection 10 was not found")


def _parse_collection(payload: bytes) -> tuple[list[tuple[int, int, int]], list[int]]:
    if len(payload) < 544:
        raise FormatError("collection 10 header is truncated")
    color_count = struct.unpack_from(">H", payload, 6)[0]
    clut_count = struct.unpack_from(">H", payload, 8)[0]
    color_offset = struct.unpack_from(">I", payload, 10)[0]
    bitmap_count = struct.unpack_from(">H", payload, 26)[0]
    bitmap_table = struct.unpack_from(">I", payload, 28)[0]
    if clut_count < 1 or color_count > 256:
        raise FormatError("collection 10 has an unsupported color table")

    palette = [(0, 0, 0)] * 256
    for index in range(color_count):
        at = color_offset + index * 8
        if at + 8 > len(payload):
            raise FormatError("collection 10 color table is truncated")
        value, red, green, blue = struct.unpack_from(">4H", payload, at)
        palette[value & 0xFF] = (red >> 8, green >> 8, blue >> 8)

    bitmap_offsets = []
    for index in range(bitmap_count):
        at = bitmap_table + index * 4
        if at + 4 > len(payload):
            raise FormatError("collection 10 bitmap table is truncated")
        bitmap_offsets.append(struct.unpack_from(">I", payload, at)[0])
    return palette, bitmap_offsets


def _bitmap_info(payload: bytes, offset: int) -> tuple[int, int, int, int]:
    if offset + 26 > len(payload):
        raise FormatError("bitmap header is truncated")
    width, height, bytes_per_row, flags, depth = struct.unpack_from(">5h", payload, offset)
    if depth != 8 or bytes_per_row < width or flags & 0x8000:
        raise FormatError("menu bitmap must be an uncompressed row-order 8-bit bitmap")
    pixel_offset = offset + 26 + (height + 1) * 4
    if pixel_offset + bytes_per_row * height > len(payload):
        raise FormatError("menu bitmap pixels are truncated")
    return width, height, bytes_per_row, pixel_offset


def unpack_shapes(source: Path, destination: Path) -> int:
    blob = source.read_bytes()
    payload = _collection_payload(blob)
    palette, bitmap_offsets = _parse_collection(payload)
    # First expose PICT resources using the normal resource-fork workflow.
    unpack_resources(source, destination)
    editable = destination / "editable" / "collection_10"
    editable.mkdir(parents=True, exist_ok=True)

    flat_palette = [component for color in palette for component in color]
    entries = []
    for index, offset in enumerate(bitmap_offsets):
        width, height, bytes_per_row, pixel_offset = _bitmap_info(payload, offset)
        pixels = bytearray()
        for row in range(height):
            start = pixel_offset + row * bytes_per_row
            pixels += payload[start : start + width]
        image = Image.frombytes("P", (width, height), bytes(pixels))
        image.putpalette(flat_palette)
        filename = f"shape_{index:02d}.png"
        path = editable / filename
        image.save(path)
        entries.append(
            {
                "shape": index,
                "bitmap": index,
                "width": width,
                "height": height,
                "file": f"editable/collection_10/{filename}",
                "sha256": _sha256(path),
            }
        )

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    manifest["format"] = "marathon-1-shapes"
    manifest["collection"] = COLLECTION
    manifest["shape_images"] = entries
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pict_count = sum(1 for entry in manifest["resources"] if entry.get("editable_file"))
    return len(entries) + pict_count


def _locate_resource(blob: bytes) -> tuple[int, int]:
    fork, container = split_container(blob)
    fork_start = 0
    if container["kind"] == "macbinary":
        data_length = struct.unpack_from(">I", blob, 83)[0]
        fork_start = 128 + ((data_length + 127) & ~127)
    data_offset, map_offset = struct.unpack_from(">2I", fork)
    type_list = map_offset + struct.unpack_from(">H", fork, map_offset + 24)[0]
    type_count = struct.unpack_from(">H", fork, type_list)[0] + 1
    for type_index in range(type_count):
        entry = type_list + 2 + type_index * 8
        resource_type = fork[entry : entry + 4]
        count = struct.unpack_from(">H", fork, entry + 4)[0] + 1
        refs = type_list + struct.unpack_from(">H", fork, entry + 6)[0]
        for index in range(count):
            ref = refs + index * 12
            resource_id = struct.unpack_from(">h", fork, ref)[0]
            if resource_type == RESOURCE_TYPE and resource_id == RESOURCE_ID:
                relative = int.from_bytes(fork[ref + 5 : ref + 8], "big")
                length_at = fork_start + data_offset + relative
                length = struct.unpack_from(">I", blob, length_at)[0]
                return length_at + 4, length
    raise FormatError("Marathon 1 menu collection 10 was not found")


def pack_shapes(source: Path, destination: Path) -> int:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    pict_changed = sum(
        1
        for entry in manifest["resources"]
        if entry.get("editable_file")
        and _sha256(source / entry["editable_file"]) != entry.get("editable_sha256")
    )
    # Rebuild only when a PICT changed; otherwise preserve the original container
    # byte-for-byte and patch collection pixels directly.
    if pict_changed:
        with tempfile.TemporaryDirectory() as temporary:
            resource_output = Path(temporary) / "Shapes-with-picts.shps"
            pack_resources(source, resource_output)
            blob = bytearray(resource_output.read_bytes())
    else:
        blob = bytearray((source / manifest["original_file"]).read_bytes())
    payload_start, payload_length = _locate_resource(blob)
    payload = bytearray(blob[payload_start : payload_start + payload_length])
    palette, bitmap_offsets = _parse_collection(payload)

    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette([component for color in palette for component in color])
    changed = pict_changed
    for entry in manifest["shape_images"]:
        path = source / entry["file"]
        if _sha256(path) == entry["sha256"]:
            continue
        with Image.open(path) as opened:
            if opened.size != (entry["width"], entry["height"]):
                raise FormatError(
                    f"{entry['file']} must remain {entry['width']}x{entry['height']} pixels"
                )
            if opened.mode == "P" and opened.getpalette() == palette_image.getpalette():
                image = opened.copy()
            else:
                image = opened.convert("RGB").quantize(
                    palette=palette_image, dither=Image.Dither.NONE
                )
        offset = bitmap_offsets[entry["bitmap"]]
        width, height, bytes_per_row, pixel_offset = _bitmap_info(payload, offset)
        pixels = image.tobytes()
        for row in range(height):
            start = pixel_offset + row * bytes_per_row
            payload[start : start + width] = pixels[row * width : (row + 1) * width]
        changed += 1

    blob[payload_start : payload_start + payload_length] = payload
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(blob)
    return changed
