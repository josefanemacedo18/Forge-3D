using System.Globalization;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace Forge3D.Revit.Infrastructure;

/// <summary>Ícones da faixa de opções desenhados por código (sem arquivos de imagem).</summary>
internal static class Icons
{
    public static ImageSource Make(string glyph, Color background, int size)
    {
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            var rect = new Rect(0.5, 0.5, size - 1, size - 1);
            dc.DrawRoundedRectangle(new SolidColorBrush(background), new Pen(new SolidColorBrush(Darker(background)), 1), rect, size * 0.2, size * 0.2);
            var typeface = new Typeface(new FontFamily("Segoe UI"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
            double em = glyph.Length > 2 ? size * 0.36 : size * 0.48;
            var text = new FormattedText(glyph, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, typeface, em, Brushes.White, 1.0);
            dc.DrawText(text, new Point((size - text.Width) / 2, (size - text.Height) / 2));
        }
        var bmp = new RenderTargetBitmap(size, size, 96, 96, PixelFormats.Pbgra32);
        bmp.Render(dv);
        bmp.Freeze();
        return bmp;
    }

    private static Color Darker(Color c) => Color.FromRgb((byte)(c.R * 0.75), (byte)(c.G * 0.75), (byte)(c.B * 0.75));

    public static readonly Color Orange = Color.FromRgb(0xE8, 0x59, 0x0C);
    public static readonly Color Blue = Color.FromRgb(0x1C, 0x7E, 0xD6);
    public static readonly Color Green = Color.FromRgb(0x2F, 0x9E, 0x44);
    public static readonly Color Slate = Color.FromRgb(0x49, 0x50, 0x57);
    public static readonly Color Purple = Color.FromRgb(0x70, 0x48, 0xE8);
}
