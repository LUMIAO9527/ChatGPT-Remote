"""Export light and dark Rm assets from one canonical vector."""
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def vector(theme='dark') -> str:
    source = (HERE / 'assets/rm-mark.svg').read_text(encoding='utf-8').strip()
    if theme == 'light':
        return (source.replace('fill="#171717"', 'fill="#ffffff" stroke="#d7d7d7" stroke-width="0.75"')
                .replace('stroke="#ffffff"', 'stroke="#171717"'))
    if theme == 'auto':
        style = ('<style>@media(prefers-color-scheme:dark){'
                 '#rm-tile{fill:#fff;stroke:#d7d7d7;stroke-width:.75}'
                 '#rm-letters{stroke:#171717}}</style>')
        return source.replace('</svg>', style + '</svg>')
    return source


def build():
    from PIL import Image
    images = {}
    for theme in ('light', 'dark'):
        source = HERE / ('assets/rm-mark-' + theme + '.svg')
        source.write_text(vector(theme) + '\n', encoding='utf-8')
        raster = HERE.parent / '_wip' / 'icon-render' / ('rm-' + theme + '.png')
        subprocess.run(['powershell.exe', '-NoProfile', '-Sta', '-ExecutionPolicy', 'Bypass',
                        '-File', str(HERE / 'render_mark.ps1'), '-Source', str(source),
                        '-Output', str(raster)], check=True)
        with Image.open(raster) as rendered:
            image = rendered.convert('RGBA')
        image.save(HERE / ('desktop-' + theme + '.ico'), format='ICO',
                   sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
        images[theme] = image
    (HERE / 'desktop.ico').write_bytes((HERE / 'desktop-dark.ico').read_bytes())
    return images['dark'].resize((256, 256), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    build()
