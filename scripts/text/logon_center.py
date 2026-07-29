"""Compensate for Marathon's incorrect logon-title width calculation.

The engine draws terminal text after converting Shift-JIS (and the Korean
escape sequences) to UTF-8, but draw_logon_text() calculates the centering
width from the unconverted bytes.  Prefixing the title with spaces moves the
visible text back to the right.  The amount is calculated with the same
SDL_ttf glyph advances used by the game.
"""

import ctypes
import ctypes.util
from pathlib import Path

import termcodec


FONT_SIZE = 12
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FONT = SCRIPT_DIR / "Fonts.ttf"


def _load_sdl_ttf():
    candidates = [
        ctypes.util.find_library("SDL2_ttf"),
        "/opt/homebrew/lib/libSDL2_ttf.dylib",
        "/usr/local/lib/libSDL2_ttf.dylib",
        "SDL2_ttf.dll",
        "libSDL2_ttf.so",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            pass
    raise RuntimeError("SDL2_ttf library not found; cannot calculate logon padding")


class FontWidths:
    def __init__(self, font_path=DEFAULT_FONT, size=FONT_SIZE):
        self.lib = _load_sdl_ttf()
        self.lib.TTF_Init.restype = ctypes.c_int
        self.lib.TTF_OpenFont.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.lib.TTF_OpenFont.restype = ctypes.c_void_p
        self.lib.TTF_CloseFont.argtypes = [ctypes.c_void_p]
        self.lib.TTF_GlyphMetrics32.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.lib.TTF_GlyphMetrics32.restype = ctypes.c_int

        if self.lib.TTF_Init() != 0:
            raise RuntimeError("TTF_Init failed")
        self.font = self.lib.TTF_OpenFont(str(font_path).encode(), size)
        if not self.font:
            raise RuntimeError(f"could not open font: {font_path}")
        self.cache = {}

    def close(self):
        if self.font:
            self.lib.TTF_CloseFont(self.font)
            self.font = None

    def advance(self, codepoint):
        if codepoint not in self.cache:
            value = ctypes.c_int()
            result = self.lib.TTF_GlyphMetrics32(
                self.font, codepoint, None, None, None, None, ctypes.byref(value)
            )
            if result != 0:
                # SDL_ttf uses the font's missing-glyph advance in this case.
                missing = ctypes.c_int()
                self.lib.TTF_GlyphMetrics32(
                    self.font, 0, None, None, None, None, ctypes.byref(missing)
                )
                value = missing
            self.cache[codepoint] = value.value
        return self.cache[codepoint]

    def text_width(self, text):
        return sum(self.advance(ord(ch)) for ch in text)

    def raw_engine_width(self, data):
        """Reproduce next_utf8() as used on the unconverted resource bytes."""
        width = 0
        i = 0
        wide_advance = self.advance(ord("가"))
        while i < len(data):
            c = data[i]
            if c < 0x80:
                codepoint, step = c, 1
            elif c < 0xE0:
                following = data[i + 1] if i + 1 < len(data) else 0
                codepoint = ((c & 0x1F) << 6) | (following & 0x3F)
                step = 2
            else:
                b1 = data[i + 1] if i + 1 < len(data) else 0
                b2 = data[i + 2] if i + 2 < len(data) else 0
                codepoint = ((c & 0x0F) << 12) | ((b1 & 0x3F) << 6) | (b2 & 0x3F)
                step = 3
            advance = self.advance(codepoint)
            # Windows SDL_ttf renders the invalid code points manufactured
            # from a Korean escape as full-width replacement glyphs.  Some
            # other FreeType builds report a half-width .notdef advance here;
            # using that value would hide the bug and calculate no padding.
            if c >= 0x80:
                advance = max(advance, wide_advance)
            width += advance
            i += step
        return width


def pad_logon_title(text, widths):
    """Add calculated spaces to the first text line after every #logon."""
    lines = text.splitlines(keepends=True)
    pad_next = False
    changes = []

    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if body.lower().startswith("#logon"):
            pad_next = True
            continue
        if not pad_next:
            continue
        if body.startswith(";") or not body:
            continue

        # Source files are kept clean; padding exists only in the packed data.
        title = body.lstrip(" ")
        encoded = termcodec.encode_all_escaped(title)
        actual = widths.text_width(title)
        measured = widths.raw_engine_width(encoded)
        space_width = widths.advance(ord(" "))
        spaces = max(0, round((measured - actual) / space_width))
        lines[index] = (" " * spaces) + title + ending
        changes.append((title, spaces, measured, actual))
        pad_next = False

    return "".join(lines), changes
