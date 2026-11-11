using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Automation;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ChatGPTRemote.Native
{
    public sealed class RailAnchor : EventArgs
    {
        public Rectangle Button { get; private set; }
        public Rectangle Client { get; private set; }
        internal IntPtr Window { get; private set; }
        internal uint Dpi { get; private set; }

        internal RailAnchor(Rectangle button, Rectangle client, IntPtr window, uint dpi)
        {
            Button = button;
            Client = client;
            Window = window;
            Dpi = dpi;
        }
    }

    public sealed class RailButton : IDisposable
    {
        private const string PackageFamily = "OpenAI.Codex_2p2nqsd0c76g0";
        private readonly IntPtr panel;
        private readonly bool attached;
        private readonly RailForm form = new RailForm();
        private readonly Timer timer = new Timer { Interval = 140 };
        private readonly ToolTip tip = new ToolTip { InitialDelay = 450, ReshowDelay = 450 };
        private IntPtr inspected, target, sampledWindow;
        private Rectangle sampledClient;
        private uint sampledDpi;
        private Task<RailAnchor> pending;
        private DateTime nextProbe, nextDiscovery;
        private RailAnchor anchor;

        public event EventHandler<RailAnchor> Clicked;
        public event EventHandler<RailAnchor> PositionChanged;

        public RailButton(long panelHandle) : this(panelHandle, false) { }

        internal RailButton(long panelHandle, bool attached)
        {
            panel = new IntPtr(panelHandle);
            this.attached = attached;
            if (attached)
            {
                form.TopMost = false;
                form.Button.FollowTheme = true;
            }
            tip.SetToolTip(form.Button, "Remote · 手机连接");
            form.Button.Click += delegate
            {
                var handler = Clicked;
                if (handler != null && anchor != null && form.Visible)
                    handler(this, anchor);
            };
            timer.Tick += Tick;
            timer.Start();
        }

        private void Tick(object sender, EventArgs args)
        {
            var foreground = GetForegroundWindow();
            if (foreground != panel && GetAncestor(foreground, 3) != panel && foreground != inspected)
            {
                inspected = foreground;
                if (IsChatGptWindow(foreground))
                    SetTarget(foreground);
                else if (!attached)
                    SetTarget(IntPtr.Zero);
            }
            if (attached && !IsVisibleWindow(target) && DateTime.UtcNow >= nextDiscovery)
            {
                nextDiscovery = DateTime.UtcNow.AddSeconds(2);
                SetTarget(FindChatGptWindow());
            }

            Rectangle client;
            if ((!attached && foreground == IntPtr.Zero) || !IsVisibleWindow(target) || !TryGetClient(target, out client))
            {
                Hide();
                return;
            }

            var dpi = GetDpiForWindow(target);
            if (anchor != null && anchor.Client != client)
            {
                nextProbe = DateTime.MinValue;
                if (anchor.Dpi == dpi && client.Width >= anchor.Button.Right - anchor.Client.Left &&
                    client.Height >= anchor.Client.Bottom - anchor.Button.Top)
                {
                    var moved = anchor.Button;
                    moved.Offset(client.Left - anchor.Client.Left, client.Bottom - anchor.Client.Bottom);
                    Apply(new RailAnchor(moved, client, target, dpi));
                }
                else
                    Hide();
            }
            if (pending != null && pending.IsCompleted)
            {
                if (pending.Status == TaskStatus.RanToCompletion && sampledWindow == target &&
                    sampledClient == client && sampledDpi == dpi)
                    Apply(pending.Result);
                else if (pending.IsFaulted)
                {
                    GC.KeepAlive(pending.Exception);
                    Hide();
                }
                pending = null;
            }

            if (pending != null || (anchor != null && DateTime.UtcNow < nextProbe))
                return;
            if (sampledWindow == target && sampledClient == client && sampledDpi == dpi &&
                DateTime.UtcNow < nextProbe)
                return;
            var handle = target;
            sampledWindow = target;
            sampledClient = client;
            sampledDpi = dpi;
            nextProbe = DateTime.UtcNow.AddSeconds(2);
            pending = Task.Run(() => Locate(handle, client, dpi));
        }

        private void SetTarget(IntPtr window)
        {
            if (target == window)
                return;
            target = window;
            nextProbe = DateTime.MinValue;
            Hide();
        }

        private static bool IsVisibleWindow(IntPtr window)
        {
            return window != IntPtr.Zero && IsWindowVisible(window) && !IsIconic(window);
        }

        private static IntPtr FindChatGptWindow()
        {
            var found = IntPtr.Zero;
            EnumWindows(delegate(IntPtr window, IntPtr context)
            {
                if (!IsVisibleWindow(window) || GetWindow(window, 4) != IntPtr.Zero || !IsChatGptWindow(window))
                    return true;
                found = window;
                return false;
            }, IntPtr.Zero);
            return found;
        }

        private void Hide()
        {
            if (anchor == null)
                return;
            anchor = null;
            if (form.Visible)
                form.Hide();
            NotifyPosition();
        }

        private void Apply(RailAnchor position)
        {
            if (position == null)
            {
                Hide();
                return;
            }
            bool changed = anchor == null || anchor.Client != position.Client ||
                anchor.Button != position.Button || anchor.Dpi != position.Dpi;
            anchor = position;
            if (attached)
                form.Button.Dark = UsesDarkTheme(position.Window);
            if (form.Bounds != position.Button)
                form.Bounds = position.Button;
            if (!form.Visible)
            {
                if (attached && position.Window != IntPtr.Zero)
                {
                    var aboveOwner = GetWindow(position.Window, 3);
                    form.Show(new WindowHandle(position.Window));
                    if (aboveOwner != IntPtr.Zero && aboveOwner != form.Handle)
                        // Preserve the size, position, focus and z-order of the owner.
                        SetWindowPos(form.Handle, aboveOwner, 0, 0, 0, 0, 0x213);
                }
                else
                    form.Show();
            }
            if (changed)
                NotifyPosition();
        }

        internal void Highlight(bool active)
        {
            form.Button.Active = active;
            form.Button.Invalidate();
        }

        private void NotifyPosition()
        {
            var handler = PositionChanged;
            if (handler != null)
                handler(this, anchor);
        }

        public void Dispose()
        {
            timer.Stop();
            timer.Dispose();
            tip.Dispose();
            form.Dispose();
        }

        private static Condition Named(string english, string chinese)
        {
            return new OrCondition(
                new PropertyCondition(AutomationElement.NameProperty, english),
                new PropertyCondition(AutomationElement.NameProperty, chinese));
        }

        private static Rectangle Bounds(AutomationElement element)
        {
            var rect = element.Current.BoundingRectangle;
            if (rect.IsEmpty || element.Current.IsOffscreen ||
                double.IsNaN(rect.X) || double.IsNaN(rect.Y))
                return Rectangle.Empty;
            return Rectangle.FromLTRB((int)Math.Round(rect.Left), (int)Math.Round(rect.Top),
                (int)Math.Round(rect.Right), (int)Math.Round(rect.Bottom));
        }

        private static RailAnchor Locate(IntPtr window, Rectangle client, uint dpi)
        {
            var root = AutomationElement.FromHandle(window);
            var nav = root.FindFirst(TreeScope.Descendants, Named("App navigation", "应用导航"));
            if (nav == null)
            {
                // Some desktop builds expose only the Chromium window frame
                // over UIA. Its footer uses the same 50px rail and 70px inset.
                if (root.FindFirst(TreeScope.Descendants, new PropertyCondition(
                    AutomationElement.ControlTypeProperty, ControlType.Document)) != null)
                    return null;
                var frameSlot = FrameSlot(client, dpi);
                return frameSlot.IsEmpty ? null : new RailAnchor(frameSlot, client, window, dpi);
            }
            var buttonType = new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button);
            var usage = nav.FindFirst(TreeScope.Descendants, new AndCondition(buttonType, Named("Usage", "用量")));
            if (usage == null)
                return null;
            var measured = Bounds(usage);
            var position = CalculatePosition(measured, client, dpi);
            if (position.IsEmpty)
                return null;
            foreach (AutomationElement button in nav.FindAll(TreeScope.Descendants, buttonType))
                if (position.IntersectsWith(Bounds(button)))
                    return null;
            return new RailAnchor(position, client, window, dpi);
        }

        private static Rectangle FrameSlot(Rectangle client, uint dpi)
        {
            double scale = (dpi == 0 ? 96 : dpi) / 96.0;
            if (client.Width < 480 * scale || client.Height < 490 * scale)
                return Rectangle.Empty;
            int button = (int)Math.Round(36 * scale);
            var usage = new Rectangle(client.Left + (int)Math.Round(7 * scale),
                client.Bottom - (int)Math.Round(88 * scale), button, button);
            return CalculatePosition(usage, client, dpi);
        }

        public static Rectangle CalculatePosition(Rectangle usage, Rectangle client, uint dpi)
        {
            double scale = (dpi == 0 ? 96 : dpi) / 96.0;
            int size = Math.Min(Math.Min(usage.Width, usage.Height), (int)Math.Round(28 * scale));
            int gap = Math.Max(4, (int)Math.Round(size / 6.0));
            if (size < 16 || !client.Contains(usage) || usage.Left - client.Left > 80 * scale ||
                client.Bottom - usage.Bottom > 144 * scale)
                return Rectangle.Empty;
            var position = new Rectangle(usage.Left + (usage.Width - size) / 2, usage.Top - gap - size, size, size);
            return client.Contains(position) ? position : Rectangle.Empty;
        }

        internal static bool UsesDarkTheme(IntPtr window)
        {
            int dark;
            if (window != IntPtr.Zero && DwmGetWindowAttribute(window, 20, out dark, sizeof(int)) == 0)
                return dark != 0;
            return !Equals(Registry.GetValue(
                @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "AppsUseLightTheme", 0), 1);
        }

        internal sealed class WindowHandle : IWin32Window
        {
            public IntPtr Handle { get; private set; }
            public WindowHandle(IntPtr handle) { Handle = handle; }
        }

        private static bool TryGetClient(IntPtr window, out Rectangle client)
        {
            client = Rectangle.Empty;
            var origin = new Point();
            RECT rect;
            if (!GetClientRect(window, out rect) || !ClientToScreen(window, ref origin))
                return false;
            client = new Rectangle(origin.X, origin.Y, rect.Right, rect.Bottom);
            return client.Width > 0 && client.Height > 0;
        }

        private static bool IsChatGptWindow(IntPtr window)
        {
            var type = new StringBuilder(48);
            if (GetClassName(window, type, type.Capacity) == 0 || type.ToString() != "Chrome_WidgetWin_1")
                return false;
            uint pid;
            GetWindowThreadProcessId(window, out pid);
            var process = OpenProcess(0x1000, false, pid);
            if (process == IntPtr.Zero)
                return false;
            try
            {
                uint length = 128;
                var family = new StringBuilder((int)length);
                if (GetPackageFamilyName(process, ref length, family) == 0)
                    return string.Equals(family.ToString(), PackageFamily, StringComparison.OrdinalIgnoreCase);
                // A desktop launch can omit the app identity. Accept only the
                // executable installed inside Windows' protected package store.
                var parts = ReadImagePath(process).Split(Path.DirectorySeparatorChar);
                var store = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WindowsApps")
                    .Split(Path.DirectorySeparatorChar);
                if (parts.Length != store.Length + 3)
                    return false;
                for (int i = 0; i < store.Length; ++i)
                    if (!string.Equals(parts[i], store[i], StringComparison.OrdinalIgnoreCase))
                        return false;
                return parts[store.Length].StartsWith("OpenAI.Codex_", StringComparison.OrdinalIgnoreCase) &&
                    parts[store.Length].EndsWith("_2p2nqsd0c76g0", StringComparison.OrdinalIgnoreCase) &&
                    string.Equals(parts[store.Length + 1], "app", StringComparison.OrdinalIgnoreCase) &&
                    string.Equals(parts[store.Length + 2], "ChatGPT.exe", StringComparison.OrdinalIgnoreCase);
            }
            finally { CloseHandle(process); }
        }

        internal static string ExecutablePath(IntPtr window)
        {
            uint pid;
            GetWindowThreadProcessId(window, out pid);
            var process = OpenProcess(0x1000, false, pid);
            if (process == IntPtr.Zero)
                return "";
            try { return ReadImagePath(process); }
            finally { CloseHandle(process); }
        }

        private static string ReadImagePath(IntPtr process)
        {
            var image = new StringBuilder(2048);
            uint length = (uint)image.Capacity;
            return QueryFullProcessImageName(process, 0, image, ref length) ? image.ToString() : "";
        }

        private sealed class RailForm : Form
        {
            public readonly GlyphButton Button = new GlyphButton();
            protected override bool ShowWithoutActivation { get { return true; } }
            protected override CreateParams CreateParams
            {
                get
                {
                    var cp = base.CreateParams;
                    cp.ExStyle |= 0x08000080; // WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
                    return cp;
                }
            }

            public RailForm()
            {
                FormBorderStyle = FormBorderStyle.None;
                ShowInTaskbar = false;
                StartPosition = FormStartPosition.Manual;
                AutoScaleMode = AutoScaleMode.None;
                TopMost = true;
                Text = "Remote · 手机连接";
                var transparent = Color.Magenta;
                BackColor = transparent;
                TransparencyKey = transparent;
                Button.Dock = DockStyle.Fill;
                Controls.Add(Button);
            }
        }

        private sealed class GlyphButton : Button
        {
            private bool hover;
            private bool dark = true;
            public bool FollowTheme { get; set; }
            public bool Active { get; set; }
            public bool Dark
            {
                get { return dark; }
                set { if (dark != value) { dark = value; Invalidate(); } }
            }
            public GlyphButton()
            {
                AccessibleName = "Remote · 手机连接";
                Cursor = Cursors.Hand;
                FlatStyle = FlatStyle.Flat;
                TabStop = false;
                MouseEnter += delegate { hover = true; Invalidate(); };
                MouseLeave += delegate { hover = false; Invalidate(); };
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                var graphics = e.Graphics;
                graphics.Clear(Color.Magenta);
                graphics.ScaleTransform(Width / 36f, Height / 36f);
                using (var tile = new GraphicsPath())
                {
                    tile.AddArc(1, 1, 16, 16, 180, 90);
                    tile.AddArc(19, 1, 16, 16, 270, 90);
                    tile.AddArc(19, 19, 16, 16, 0, 90);
                    tile.AddArc(1, 19, 16, 16, 90, 90);
                    tile.CloseFigure();
                    Color background;
                    if (FollowTheme)
                        background = dark
                            ? (hover || Active ? Color.FromArgb(47, 52, 63) : Color.FromArgb(26, 32, 44))
                            : (hover || Active ? Color.FromArgb(224, 226, 231) : Color.FromArgb(243, 244, 246));
                    else
                        background = hover ? Color.FromArgb(56, 62, 73) : Color.FromArgb(34, 39, 49);
                    using (var fill = new SolidBrush(background))
                        graphics.FillPath(fill, tile);
                }
                graphics.SmoothingMode = SmoothingMode.AntiAlias;
                var foreground = FollowTheme ? (dark ? Color.FromArgb(185, 187, 194) : Color.FromArgb(84, 89, 100))
                    : Color.FromArgb(244, 247, 252);
                using (var ink = new Pen(foreground, 2.3f))
                {
                    ink.StartCap = ink.EndCap = LineCap.Round;
                    graphics.DrawArc(ink, 10, 10, 16, 16, 47, 266);
                }
                using (var accent = new SolidBrush(Color.FromArgb(111, 217, 161)))
                    graphics.FillEllipse(accent, 23, 16.35f, 3.3f, 3.3f);
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT { public int Left, Top, Right, Bottom; }
        private delegate bool EnumWindowsCallback(IntPtr window, IntPtr context);
        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr context);
        [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window, uint flags);
        [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr window, uint command);
        [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr insertAfter,
            int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr window, out RECT rect);
        [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr window, ref Point point);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
        [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr window, uint attribute, out int value, int size);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder name, int capacity);
        [DllImport("kernel32.dll")] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)] private static extern int GetPackageFamilyName(IntPtr process, ref uint length, StringBuilder family);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint length);
    }
}
