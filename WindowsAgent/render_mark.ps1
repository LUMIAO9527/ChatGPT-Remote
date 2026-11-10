[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Output,
    [ValidateRange(16, 2048)][int]$Size = 1024
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore, WindowsBase
[xml]$svg = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Source))
if ($svg.DocumentElement.GetAttribute('viewBox') -ne '0 0 32 32' -or
    $svg.SelectNodes('//*[@transform or local-name()="text" or local-name()="image"]').Count) {
    throw 'Expected a self-contained 32-unit mark without transforms, fonts or images.'
}
$brush = [Windows.Media.BrushConverter]::new()
function Brush([string]$value) {
    if (!$value -or $value -eq 'none') { return $null }
    return $brush.ConvertFromString($value)
}
$visual = [Windows.Media.DrawingVisual]::new()
$drawing = $visual.RenderOpen()
$drawing.PushTransform([Windows.Media.ScaleTransform]::new($Size / 32.0, $Size / 32.0))
foreach ($node in $svg.DocumentElement.ChildNodes) {
    if ($node.NodeType -ne 'Element') { continue }
    $fill = Brush $node.GetAttribute('fill')
    $strokeValue = $node.GetAttribute('stroke')
    $pen = $null
    if ($strokeValue -and $strokeValue -ne 'none') {
        $pen = [Windows.Media.Pen]::new((Brush $strokeValue), [double]$node.GetAttribute('stroke-width'))
        $pen.StartLineCap = 'Round'
        $pen.EndLineCap = 'Round'
        $pen.LineJoin = 'Round'
    }
    switch ($node.LocalName) {
        'path' { $geometry = [Windows.Media.Geometry]::Parse($node.GetAttribute('d')) }
        'circle' {
            $geometry = [Windows.Media.EllipseGeometry]::new(
                [Windows.Point]::new([double]$node.GetAttribute('cx'), [double]$node.GetAttribute('cy')),
                [double]$node.GetAttribute('r'), [double]$node.GetAttribute('r'))
        }
        'rect' {
            $rect = [Windows.Rect]::new(
                [double]$node.GetAttribute('x'), [double]$node.GetAttribute('y'),
                [double]$node.GetAttribute('width'), [double]$node.GetAttribute('height'))
            $radius = $node.GetAttribute('rx')
            if (!$radius) { $radius = '0' }
            $geometry = [Windows.Media.RectangleGeometry]::new($rect, [double]$radius, [double]$radius)
        }
        default { continue }
    }
    if ($fill -or $pen) { $drawing.DrawGeometry($fill, $pen, $geometry) }
}
$drawing.Close()
$bitmap = [Windows.Media.Imaging.RenderTargetBitmap]::new($Size, $Size, 96, 96, 'Pbgra32')
$bitmap.Render($visual)
$encoder = [Windows.Media.Imaging.PngBitmapEncoder]::new()
$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
$destination = [IO.Path]::GetFullPath($Output)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
$stream = [IO.File]::Create($destination)
try { $encoder.Save($stream) } finally { $stream.Dispose() }