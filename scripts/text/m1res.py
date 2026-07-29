"""
MacBinary II + Mac resource fork reader/writer for Marathon.appl.

Aleph One validates the MacBinary CRC-16 before it will touch the resource
fork (resource_manager.cpp:107), so any rewrite has to recompute it.
"""

import struct

MACBINARY_HEADER = 128
DATA_LEN_OFF = 83
RSRC_LEN_OFF = 87
CRC_OFF = 124


def crc16(data):
    """CRC-16/XMODEM, as implemented in is_macbinary()."""
    crc = 0
    for byte in data:
        d = byte << 8
        for _ in range(8):
            if (d ^ crc) & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
            d = (d << 1) & 0xFFFF
    return crc


def pad128(n):
    return (n + 0x7F) & ~0x7F


class Resource:
    __slots__ = ("type", "id", "name", "attrs", "data")

    def __init__(self, rtype, rid, name, attrs, data):
        self.type = rtype
        self.id = rid
        self.name = name        # bytes or None
        self.attrs = attrs
        self.data = data

    def __repr__(self):
        return f"<{self.type.decode('latin1')} {self.id} {len(self.data)}B>"


class MacBinaryFile:
    """Parses a MacBinary II file and rebuilds it with a modified resource fork."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.raw = f.read()

        self.header = bytearray(self.raw[:MACBINARY_HEADER])
        if self.header[0] != 0 or self.header[74] != 0:
            raise ValueError("not a MacBinary file")

        stored_crc = struct.unpack(">H", self.header[CRC_OFF:CRC_OFF + 2])[0]
        if crc16(self.header[:CRC_OFF]) != stored_crc:
            raise ValueError("MacBinary CRC mismatch on input file")

        self.data_len = struct.unpack(">I", self.raw[DATA_LEN_OFF:DATA_LEN_OFF + 4])[0]
        self.rsrc_len = struct.unpack(">I", self.raw[RSRC_LEN_OFF:RSRC_LEN_OFF + 4])[0]

        self.data_fork = self.raw[MACBINARY_HEADER:MACBINARY_HEADER + self.data_len]
        self.rsrc_start = MACBINARY_HEADER + pad128(self.data_len)
        rsrc = self.raw[self.rsrc_start:self.rsrc_start + self.rsrc_len]

        self.type_order, self.resources = _parse_rsrc(rsrc)

    def get(self, rtype, rid):
        for r in self.resources:
            if r.type == rtype and r.id == rid:
                return r
        return None

    def by_type(self, rtype):
        return [r for r in self.resources if r.type == rtype]

    def write(self, path):
        rsrc = _build_rsrc(self.type_order, self.resources)

        header = bytearray(self.header)
        struct.pack_into(">I", header, RSRC_LEN_OFF, len(rsrc))
        struct.pack_into(">H", header, CRC_OFF, crc16(header[:CRC_OFF]))

        out = bytearray()
        out += header
        out += self.data_fork
        out += b"\0" * (pad128(self.data_len) - self.data_len)
        out += rsrc
        out += b"\0" * (pad128(len(rsrc)) - len(rsrc))

        with open(path, "wb") as f:
            f.write(out)


def _parse_rsrc(rsrc):
    data_off, map_off, _, _ = struct.unpack(">IIII", rsrc[:16])
    m = map_off
    type_list_off = struct.unpack(">H", rsrc[m + 24:m + 26])[0]
    name_list_off = struct.unpack(">H", rsrc[m + 26:m + 28])[0]
    tl = m + type_list_off
    nl = m + name_list_off

    ntypes = struct.unpack(">H", rsrc[tl:tl + 2])[0] + 1
    type_order = []
    resources = []

    for i in range(ntypes):
        p = tl + 2 + i * 8
        rtype = rsrc[p:p + 4]
        count = struct.unpack(">H", rsrc[p + 4:p + 6])[0] + 1
        ref_off = struct.unpack(">H", rsrc[p + 6:p + 8])[0]
        type_order.append(rtype)

        for j in range(count):
            r = tl + ref_off + j * 12
            rid = struct.unpack(">h", rsrc[r:r + 2])[0]
            name_ofs = struct.unpack(">h", rsrc[r + 2:r + 4])[0]
            attrs = rsrc[r + 4]
            doff = struct.unpack(">I", b"\0" + rsrc[r + 5:r + 8])[0]

            name = None
            if name_ofs != -1:
                np = nl + name_ofs
                name = rsrc[np + 1:np + 1 + rsrc[np]]

            dp = data_off + doff
            length = struct.unpack(">I", rsrc[dp:dp + 4])[0]
            resources.append(Resource(rtype, rid, name, attrs, rsrc[dp + 4:dp + 4 + length]))

    return type_order, resources


def _build_rsrc(type_order, resources):
    by_type = {}
    for r in resources:
        by_type.setdefault(r.type, []).append(r)
    # keep any type that appeared in the original, in original order
    types = [t for t in type_order if t in by_type]

    # --- resource data blob ---
    blob = bytearray()
    offsets = {}
    for t in types:
        for r in by_type[t]:
            offsets[id(r)] = len(blob)
            blob += struct.pack(">I", len(r.data))
            blob += r.data

    # --- name list ---
    names = bytearray()
    name_offsets = {}
    for t in types:
        for r in by_type[t]:
            if r.name is None:
                name_offsets[id(r)] = -1
            else:
                name_offsets[id(r)] = len(names)
                names += bytes([len(r.name)]) + r.name

    # --- type list + ref lists ---
    type_list_size = 2 + len(types) * 8
    ref_lists = bytearray()
    type_entries = bytearray()
    type_entries += struct.pack(">H", len(types) - 1)

    for t in types:
        rs = by_type[t]
        ref_off = type_list_size + len(ref_lists)
        type_entries += t + struct.pack(">HH", len(rs) - 1, ref_off)
        for r in rs:
            ref_lists += struct.pack(">hh", r.id, name_offsets[id(r)])
            ref_lists += bytes([r.attrs]) + offsets[id(r)].to_bytes(3, "big")
            ref_lists += b"\0\0\0\0"

    type_list = type_entries + ref_lists

    map_body_off = 28                      # 16 reserved + 4 handle + 2 ref + 2 attrs + 2 + 2
    type_list_off = map_body_off
    name_list_off = type_list_off + len(type_list)
    map_len = name_list_off + len(names)

    data_off = 256
    map_off = data_off + len(blob)

    rsrc = bytearray()
    rsrc += struct.pack(">IIII", data_off, map_off, len(blob), map_len)
    rsrc += b"\0" * (data_off - 16)
    rsrc += blob

    rmap = bytearray()
    rmap += b"\0" * 16                     # reserved copy of header
    rmap += b"\0" * 4                      # reserved handle
    rmap += b"\0" * 2                      # reserved file ref
    rmap += struct.pack(">H", 0)           # fork attributes
    rmap += struct.pack(">HH", type_list_off, name_list_off)
    rmap += type_list
    rmap += names
    assert len(rmap) == map_len, (len(rmap), map_len)

    rsrc += rmap
    return bytes(rsrc)
