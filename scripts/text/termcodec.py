"""
Encoding for Marathon.appl 'term' resources.

The pfhore engine decodes terminal text with sjis2utf8() (csstrings.cpp:448).
When iconv fails on a byte, three escape prefixes pass raw UTF-8 through:

    0xFD <filler> b0 b1        -> 2-byte UTF-8 char   (consumes 5)
    0xFE <filler> b0 b1 b2     -> 3-byte UTF-8 char   (consumes 5)
    0xFF <filler> b0 b1 b2 b3  -> 4-byte UTF-8 char   (consumes 6)

<filler> is skipped unconditionally by both sjis2utf8() and sjisChar(), so its
value is free. We use 0x80: it can never collide with the terminal compiler's
ASCII markup ('$', '#', ';') or with MAC_LINE_END (0x0D).

Hangul syllables are 3-byte UTF-8, so they take the 0xFE form and cost 5 bytes
each. This needs no engine rebuild.
"""

MAC_LINE_END = 0x0D
FILLER = 0x80
_PREFIX = {2: 0xFD, 3: 0xFE, 4: 0xFF}


def encode(text):
    """UTF-8 str -> escaped bytes for a 'term' resource."""
    out = bytearray()
    for ch in text.replace("\r\n", "\n").replace("\n", "\r"):
        b = ch.encode("utf-8")
        if len(b) == 1:
            out += b
            continue
        try:
            # Japanese that already round-trips through Shift-JIS stays compact.
            sjis = ch.encode("shift_jis")
        except UnicodeEncodeError:
            sjis = None
        if sjis is not None:
            out += sjis
        else:
            out.append(_PREFIX[len(b)])
            out.append(FILLER)
            out += b
    return bytes(out)


def encode_all_escaped(text):
    """Like encode(), but never uses Shift-JIS -- every non-ASCII char escapes.

    Use this when the source is Korean: it keeps the byte stream unambiguous
    and avoids any dependence on the platform's Shift-JIS tables.
    """
    out = bytearray()
    for ch in text.replace("\r\n", "\n").replace("\n", "\r"):
        b = ch.encode("utf-8")
        if len(b) == 1:
            out += b
        else:
            out.append(_PREFIX[len(b)])
            out.append(FILLER)
            out += b
    return bytes(out)


def decode(data):
    """'term' resource bytes -> UTF-8 str, mirroring sjis2utf8()."""
    out = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b in (0xFD, 0xFE, 0xFF):
            size = {0xFD: 2, 0xFE: 3, 0xFF: 4}[b]
            raw = data[i + 2:i + 2 + size]
            try:
                out.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                out.append("�")
            i += 2 + size
            continue
        if b < 0x80:
            out.append(chr(b))
            i += 1
            continue
        # Shift-JIS lead byte?
        if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC:
            try:
                out.append(data[i:i + 2].decode("shift_jis"))
                i += 2
                continue
            except UnicodeDecodeError:
                pass
        out.append(data[i:i + 1].decode("mac_roman"))
        i += 1
    return "".join(out).replace("\r", "\n")
