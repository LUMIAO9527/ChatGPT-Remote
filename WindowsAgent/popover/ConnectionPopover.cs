using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ChatGPTRemote.Native
{
    internal sealed class ConnectionPopover : Form
    {
        private const string UiHost = "remote-panel.invalid";
        private const string UiUrl = "https://remote-panel.invalid/index.html";
        private static readonly HashSet<string> Operations = new HashSet<string>(StringComparer.Ordinal)
        {
            "status", "pair", "service.start", "target.pause", "device.rename", "device.remove",
            "settings.check", "settings.update", "settings.remove"
        };

        private readonly Func<string, string, string> request;
        private readonly Action<string> copyText;
        private readonly string uiFolder;
        private readonly WebView2 view;
        private readonly Label startup;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private RailAnchor anchor;
        private bool loading, ready, dark = true;
        private int viewEpoch;
        private Pairing pair;

        internal event EventHandler Uninstalled;

        internal ConnectionPopover(Func<string, string, string> request, string profileFolder, Action<string> copyText)
        {
            this.request = request;
            this.copyText = copyText;
            uiFolder = Path.Combine(Path.GetDirectoryName(typeof(ConnectionPopover).Assembly.Location), "popover");
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            AutoScaleMode = AutoScaleMode.None;
            ShowInTaskbar = false;
            Text = "ChatGPT Remote";
            AccessibleName = "手机连接";
            ClientSize = new Size(260, 156);
            startup = new Label { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, Text = "正在打开…" };
            view = new WebView2
            {
                Dock = DockStyle.Fill,
                Visible = false,
                CreationProperties = new CoreWebView2CreationProperties { UserDataFolder = profileFolder }
            };
            Controls.Add(startup);
            Controls.Add(view);
            Deactivate += delegate { Dismiss(false); };
            ApplyTheme();
        }

        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.ExStyle |= 0x80;
                cp.ClassStyle |= 0x20000;
                return cp;
            }
        }

        internal void Toggle(RailAnchor position)
        {
            if (Visible)
            {
                Dismiss(true);
                return;
            }
            anchor = position;
            ApplyTheme();
            SetLocation();
            if (anchor.Window == IntPtr.Zero)
                Show();
            else
                Show(new RailButton.WindowHandle(anchor.Window));
            Activate();
            if (ready)
            {
                SendVisibility();
                view.Focus();
            }
            else
                Initialize();
        }

        internal void Reposition(RailAnchor position)
        {
            if (!Visible)
                return;
            if (position == null || position.Window != anchor.Window)
            {
                Dismiss(false);
                return;
            }
            anchor = position;
            if (dark != RailButton.UsesDarkTheme(anchor.Window))
            {
                ApplyTheme();
                SendVisibility();
            }
            SetLocation();
        }

        private async void Initialize()
        {
            if (loading)
                return;
            loading = true;
            try
            {
                view.Visible = true;
                startup.BringToFront();
                view.CreateControl();
                await view.EnsureCoreWebView2Async(null);
                if (IsDisposed)
                    return;
                var browser = view.CoreWebView2;
                var settings = browser.Settings;
                settings.AreHostObjectsAllowed = false;
                settings.AreDevToolsEnabled = false;
                settings.AreDefaultContextMenusEnabled = false;
                settings.AreDefaultScriptDialogsEnabled = false;
                settings.AreBrowserAcceleratorKeysEnabled = false;
                settings.IsZoomControlEnabled = false;
                settings.IsStatusBarEnabled = false;
                browser.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { e.Cancel = e.Uri != UiUrl; };
                browser.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { e.Cancel = true; };
                browser.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
                browser.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs e) { e.State = CoreWebView2PermissionState.Deny; };
                browser.WebMessageReceived += Receive;
                browser.SetVirtualHostNameToFolderMapping(UiHost, uiFolder, CoreWebView2HostResourceAccessKind.Deny);
                browser.Navigate(UiUrl);
            }
            catch (Exception error)
            {
                if (!IsDisposed)
                {
                    startup.Text = "手机连接面板加载失败";
                    Console.Error.WriteLine("Remote WebView2 initialization failed: {0} (0x{1:X8})",
                        error.GetType().Name, error.HResult);
                }
            }
            finally { loading = false; }
        }

        private async void Receive(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            if (e.Source != UiUrl || view.CoreWebView2.Source != UiUrl)
                return;
            Message message;
            try { message = json.Deserialize<Message>(e.WebMessageAsJson); }
            catch (ArgumentException) { return; }
            catch (InvalidOperationException) { return; }
            if (message == null)
                return;
            if (message.operation == "ready")
            {
                ready = true;
                SendVisibility();
                view.Visible = true;
                view.BringToFront();
                if (Visible)
                    view.Focus();
                return;
            }
            if (!ready || !Visible)
                return;
            if (message.operation == "dismiss")
            {
                Dismiss(true);
                return;
            }
            if (message.id <= 0 || String.IsNullOrEmpty(message.operation))
                return;

            int current = viewEpoch;
            Dictionary<string, object> reply;
            try
            {
                if (message.operation == "clipboard.pin" || message.operation == "clipboard.link")
                {
                    if (pair == null || pair.ExpiresAt <= Now())
                        throw new InvalidOperationException();
                    copyText(message.operation == "clipboard.pin" ? pair.Pin : pair.Url);
                    reply = new Dictionary<string, object>();
                }
                else
                {
                    if (!Operations.Contains(message.operation))
                        return;
                    var payload = message.payload ?? new Dictionary<string, object>();
                    if (message.operation == "settings.update" || message.operation == "settings.remove")
                        payload["executable"] = RailButton.ExecutablePath(anchor.Window);
                    string body = json.Serialize(payload);
                    string response = await Task.Run(() => request(message.operation, body));
                    reply = json.Deserialize<Dictionary<string, object>>(response);
                    if (current == viewEpoch && message.operation == "pair" && !reply.ContainsKey("Error"))
                        pair = json.Deserialize<Pairing>(response);
                }
            }
            catch (Exception)
            {
                reply = new Dictionary<string, object>
                {
                    { "Error", message.operation.StartsWith("clipboard.", StringComparison.Ordinal)
                        ? "复制失败" : "操作未完成，请重试" }
                };
            }
            if (!IsDisposed && view.CoreWebView2.Source == UiUrl)
                view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { message.id, reply }));
            if (message.operation == "settings.remove" && !reply.ContainsKey("Error") && !IsDisposed)
            {
                var timer = new Timer { Interval = 1100 };
                timer.Tick += delegate
                {
                    timer.Dispose();
                    if (Uninstalled != null)
                        Uninstalled(this, EventArgs.Empty);
                };
                timer.Start();
            }
        }

        private void Dismiss(bool returnFocus)
        {
            if (!Visible)
                return;
            viewEpoch++;
            pair = null;
            Hide();
            SendVisibility();
            if (returnFocus && anchor.Window != IntPtr.Zero)
                SetForegroundWindow(anchor.Window);
        }

        protected override bool ProcessCmdKey(ref System.Windows.Forms.Message message, Keys key)
        {
            if (key == Keys.Escape && !ready)
            {
                Dismiss(true);
                return true;
            }
            return base.ProcessCmdKey(ref message, key);
        }

        private void SendVisibility()
        {
            if (ready && view.CoreWebView2.Source == UiUrl)
                view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type = "visibility", visible = Visible, dark }));
        }

        internal void SyncTheme()
        {
            if (Visible && dark != RailButton.UsesDarkTheme(anchor.Window))
            {
                ApplyTheme();
                SendVisibility();
            }
        }

        private void ApplyTheme()
        {
            dark = RailButton.UsesDarkTheme(anchor == null ? IntPtr.Zero : anchor.Window);
            BackColor = dark ? Color.FromArgb(38, 39, 42) : Color.White;
            startup.BackColor = BackColor;
            startup.ForeColor = dark ? Color.Gainsboro : Color.FromArgb(50, 50, 55);
            view.DefaultBackgroundColor = BackColor;
        }

        private void SetLocation()
        {
            var viewport = Rectangle.Intersect(anchor.Client, Screen.FromRectangle(anchor.Button).WorkingArea);
            if (viewport.IsEmpty)
                viewport = anchor.Client;
            double scale = (anchor.Dpi == 0 ? 96 : anchor.Dpi) / 96.0;
            var bounds = PluginPopover.Place(anchor.Button, viewport, new Size(
                (int)Math.Round(260 * scale), (int)Math.Round(156 * scale)), anchor.Dpi);
            bool sizeChanged = Size != bounds.Size || Region == null;
            if (Bounds != bounds)
                Bounds = bounds;
            if (sizeChanged)
                RoundWindow((float)(16 * scale));
        }

        private void RoundWindow(float radius)
        {
            float diameter = Math.Min(radius * 2, Math.Min(Width, Height));
            var previous = Region;
            using (var shape = new GraphicsPath())
            {
                shape.AddArc(0, 0, diameter, diameter, 180, 90);
                shape.AddArc(Width - diameter, 0, diameter, diameter, 270, 90);
                shape.AddArc(Width - diameter, Height - diameter, diameter, diameter, 0, 90);
                shape.AddArc(0, Height - diameter, diameter, diameter, 90, 90);
                shape.CloseFigure();
                Region = new Region(shape);
            }
            if (previous != null)
                previous.Dispose();
        }

        private static long Now()
        {
            return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
        }

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr window);

        public sealed class Message
        {
            public int id { get; set; }
            public string operation { get; set; }
            public Dictionary<string, object> payload { get; set; }
        }

        public sealed class Pairing
        {
            public string Pin { get; set; }
            public string Url { get; set; }
            public long ExpiresAt { get; set; }
        }
    }
}
