"""One vector mark for the desktop panel, shortcut and native notification icon."""
from pathlib import Path

GLYPHS = [
    [('M', (12, 46)), ('L', (12, 18))],
    [('M', (12, 18)), ('L', (21, 18)), ('C', (32, 18, 32, 33, 21, 33)), ('L', (12, 33))],
    [('M', (21, 33)), ('L', (31, 46))],
    [('M', (37, 46)), ('L', (37, 30))],
    [('M', (37, 35)), ('C', (37, 28, 46, 28, 46, 35)), ('L', (46, 46))],
    [('M', (46, 35)), ('C', (46, 28, 55, 28, 55, 35)), ('L', (55, 46))],
]


def vector() -> str:
    paths = ''.join('<path d="' + ' '.join(command + ' ' + ' '.join(map(str, values))
                    for command, values in glyph) + '"/>' for glyph in GLYPHS)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none" aria-hidden="true">'
            '<rect x=".5" y=".5" width="63" height="63" rx="18" fill="#181818" stroke="#484848"/>'
            '<g stroke="#fff" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round">'
            + paths + '</g></svg>')


def build():
    from PIL import Image, ImageDraw
    root = Path(__file__).resolve().parent
    assets = root / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'rm-mark.svg').write_text(vector(), encoding='utf-8')
    scale = 8
    image = Image.new('RGBA', (64*scale, 64*scale))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((.5*scale,.5*scale,63.5*scale,63.5*scale), radius=18*scale,
                           fill='#181818', outline='#484848', width=scale)
    for glyph in GLYPHS:
        points = []
        current = (0, 0)
        for command, values in glyph:
            if command in ('M', 'L'):
                current = values
                points.append(current)
            elif command == 'C':
                start = current
                a, b, end = values[:2], values[2:4], values[4:]
                for step in range(1, 65):
                    t = step / 64
                    points.append(tuple((1-t)**3*start[i] + 3*(1-t)**2*t*a[i]
                                        + 3*(1-t)*t*t*b[i] + t**3*end[i] for i in (0,1)))
                current = end
        points = [(x*scale,y*scale) for x,y in points]
        width = round(4.6*scale)
        left=[];right=[]
        for index,(x,y) in enumerate(points):
            before=points[max(0,index-1)];after=points[min(len(points)-1,index+1)]
            dx,dy=after[0]-before[0],after[1]-before[1]
            length=(dx*dx+dy*dy)**.5 or 1
            nx,ny=-dy/length*width/2,dx/length*width/2
            left.append((x+nx,y+ny));right.append((x-nx,y-ny))
        draw.polygon(left+right[::-1],fill='white')
        for x,y in (points[0],points[-1]):
            radius=width/2;draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill='white')
    image = image.resize((256,256), Image.Resampling.LANCZOS)
    image.save(root / 'desktop.ico', sizes=[(n,n) for n in (16,20,24,32,40,48,64,128,256)])
    return image


if __name__ == '__main__':
    build()
