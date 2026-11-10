"""Export the ChatGPT Remote mark: tray glyphs and the app tile icon.

The canonical source `assets/rm-mark.svg` is a full-bleed square glyph without a
rounded container. Display variants (app tile) are generated here, never baked
into the source.
"""
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
MARK = HERE / 'assets/rm-mark.svg'
GREEN = (16, 163, 127, 255)
PLATE = (11, 12, 13, 255)


def vector(theme='dark') -> str:
    source = MARK.read_text(encoding='utf-8').strip()
    if theme == 'light':
        return source.replace('currentColor', '#171717')
    if theme == 'dark':
        return source.replace('currentColor', '#ffffff')
    if theme == 'auto':
        style = ('<style>.mk{stroke:#171717}'
                 '@media(prefers-color-scheme:dark){.mk{stroke:#ffffff}}</style>')
        return source.replace('currentColor', '#171717').replace('</svg>', style + '</svg>')
    return source


def render(source: Path, raster: Path):
    subprocess.run(['powershell.exe', '-NoProfile', '-Sta', '-ExecutionPolicy', 'Bypass',
                    '-File', str(HERE / 'render_mark.ps1'), '-Source', str(source),
                    '-Output', str(raster)], check=True)


def tile(images):
    """App icon: rounded dark tile with the white glyph and green accent."""
    from PIL import Image, ImageDraw
    glyph = images['dark'].resize((1024, 1024), Image.Resampling.LANCZOS)
    base = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    plate = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, 1023, 1023), radius=272, fill=PLATE)
    plate.alpha_composite(glyph)
    base.alpha_composite(plate)
    return base


def build():
    from PIL import Image
    images = {}
    for theme in ('light', 'dark'):
        source = HERE / ('assets/rm-mark-' + theme + '.svg')
        source.write_text(vector(theme) + '\n', encoding='utf-8')
        raster = HERE.parent / '_wip' / 'icon-render' / ('rm-' + theme + '.png')
        render(source, raster)
        with Image.open(raster) as rendered:
            image = rendered.convert('RGBA')
        image.save(HERE / ('desktop-' + theme + '.ico'), format='ICO',
                   sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
        images[theme] = image
    app = tile(images)
    app.save(HERE / 'desktop.ico', format='ICO',
             sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    return images['dark'].resize((256, 256), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    build()