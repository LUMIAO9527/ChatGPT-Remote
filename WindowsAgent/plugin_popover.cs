using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Timer = System.Windows.Forms.Timer;

namespace ChatGPTRemote.Native
{
    public static class PluginPopover
    {
        public static void Run(Func<string, string, string> request, Func<bool> stopping, string profileFolder)
        {
            Exception failure = null;
            var thread = new Thread(delegate()
            {
                try
                {
                    SetThreadDpiAwarenessContext(new IntPtr(-4)); // Per-monitor v2
                    Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException, true);
                    using (var panel = new ConnectionPopover(request, profileFolder, Clipboard.SetText))
                    using (var rail = new RailButton(panel.Handle.ToInt64(), true))
                    using (var lifetime = new Timer { Interval = 250 })
                    {
                        rail.Clicked += delegate(object sender, RailAnchor anchor) { panel.Toggle(anchor); };
                        rail.PositionChanged += delegate(object sender, RailAnchor anchor) { panel.Reposition(anchor); };
                        panel.VisibleChanged += delegate { rail.Highlight(panel.Visible); };
                        panel.Uninstalled += delegate { Application.ExitThread(); };
                        lifetime.Tick += delegate
                        {
                            if (stopping()) Application.ExitThread();
                            else panel.SyncTheme();
                        };
                        lifetime.Start();
                        if (!stopping())
                            Application.Run();
                    }
                }
                catch (Exception error) { failure = error; }
            });
            thread.IsBackground = true;
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            thread.Join();
            if (failure != null)
                throw new InvalidOperationException("Remote popover failed", failure);
        }

        public static Rectangle Place(Rectangle button, Rectangle viewport, Size size, uint dpi)
        {
            int gap = (int)Math.Round(8 * (dpi == 0 ? 96 : dpi) / 96.0);
            int width = Math.Min(size.Width, viewport.Width);
            int height = Math.Min(size.Height, viewport.Height);
            int x = button.Right + gap;
            if (x + width > viewport.Right && button.Left - gap - width >= viewport.Left)
                x = button.Left - gap - width;
            x = Math.Min(Math.Max(viewport.Left, x), viewport.Right - width);
            int y = Math.Min(Math.Max(viewport.Top, button.Bottom - height), viewport.Bottom - height);
            return new Rectangle(x, y, width, height);
        }

        [DllImport("user32.dll")]
        private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    }

}
