# Marathon Images patching

Classic Marathon `Images` files are Macintosh resource forks, often wrapped in
MacBinary. `PICT` resources are exposed directly as editable PNG files.

```sh
cd patching
uv run python unpack.py "../Marathon 2/Images.imgA" Images.unpacked

# Edit PNG files under Images.unpacked/editable/.

uv run python pack.py Images.unpacked Images-patched.imgA
```

The packer converts changed PNG files to 32-bit RGB PICT resources. Unchanged
images and non-image resources retain their exact original bytes from the hidden
`.original` reference file. `manifest.json` stores resource metadata and original
PNG hashes. The packer updates resource-fork offsets, lengths, and the MacBinary
header CRC automatically.

Marathon's indexed, 16-bit, and 32-bit PackBits PICT variants are decoded
directly. PNG reading and writing uses Pillow, installed by `uv`.

## Marathon 1 menu shapes

The same commands recognize `Shapes.shps` and expose menu collection 10:

```sh
uv run python unpack.py ../Shapes.shps Shapes.unpacked

# Edit Shapes.unpacked/editable/collection_10/shape_*.png.
# Keep each image's dimensions unchanged.

uv run python pack.py Shapes.unpacked Shapes-patched.shps
```

`shape_01.png` contains the normal menu, while `shape_11.png` and
`shape_12.png` are the pressed New Game and Load Game buttons. Changed images
are mapped back to the collection's original 8-bit palette.
