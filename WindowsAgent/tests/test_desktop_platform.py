"""Tray state, geometry and delayed callbacks without touching real apps."""
import ctypes
import os
from pathlib import Path
import secrets
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_platform import NativePanel,popup_position,PANEL_SIZE,PANEL_SIZES


class GeometryTests(unittest.TestCase):
    def test_every_route_keeps_the_same_panel_size(self):
        self.assertEqual(set(PANEL_SIZES.values()),{PANEL_SIZE})
        self.assertEqual(PANEL_SIZE,(312,458))

    def test_region_uses_a_sixteen_pixel_radius_at_each_dpi(self):
        panel=NativePanel.__new__(NativePanel);panel.user=Mock()
        dwm,gdi=Mock(),Mock();gdi.CreateRoundRectRgn.return_value=123
        with patch('desktop_platform.ctypes.WinDLL',create=True,side_effect=lambda name:dwm if name=='dwmapi' else gdi):
            panel.round_window(11,546,802,168)
        gdi.CreateRoundRectRgn.assert_called_once_with(0,0,547,803,56,56)

    def test_reopening_queries_the_moved_icon_and_the_destination_dpi(self):
        panel=NativePanel.__new__(NativePanel);panel.user=Mock();panel.anchor=(1800,1040,1824,1080)
        panel.tray_identity=(0x123456789,1);panel.user.GetDpiForWindow.return_value=96
        panel.user.MonitorFromRect.return_value=1;panel.round_window=Mock()
        def monitor(_,p):
            p._obj.rcWork=ctypes.wintypes.RECT(0,0,1920,1040)
            p._obj.rcMonitor=ctypes.wintypes.RECT(0,0,1920,1080);return 1
        panel.user.GetMonitorInfoW.side_effect=monitor
        shcore=Mock()
        def dpi(_,kind,x,y):x._obj.value=y._obj.value=168;return 0
        shcore.GetDpiForMonitor.side_effect=dpi
        form=Mock();form.Handle.ToInt64.return_value=0x987654321
        locate=Mock(side_effect=[(1800,1040,1824,1080),(1300,1040,1324,1080)])
        with patch.dict(sys.modules,{'desktop_tray':SimpleNamespace(tray_rect=locate)}), \
             patch('desktop_platform.ctypes.WinDLL',create=True,return_value=shcore):
            panel.place(form);first=panel.user.SetWindowPos.call_args.args[2:6]
            panel.place(form);second=panel.user.SetWindowPos.call_args.args[2:6]
        self.assertNotEqual(first[0],second[0]);self.assertEqual(second[2:],(546,802))
        self.assertEqual(panel.anchor,(1300,1040,1324,1080));self.assertEqual(locate.call_count,2)

    @unittest.skipUnless(os.name=='nt','Windows GDI regions')
    def test_native_region_excludes_the_square_corner_and_keeps_the_center(self):
        gdi=ctypes.WinDLL('gdi32');gdi.PtInRegion.argtypes=[ctypes.wintypes.HRGN,ctypes.c_int,ctypes.c_int]
        gdi.PtInRegion.restype=ctypes.wintypes.BOOL
        panel=NativePanel.__new__(NativePanel);panel.user=Mock()
        for dpi in (96,120,168,192):
            regions=[];panel.user.SetWindowRgn.side_effect=lambda hwnd,rgn,redraw:regions.append(rgn) or 1
            width,height=round(PANEL_SIZE[0]*dpi/96),round(PANEL_SIZE[1]*dpi/96)
            with patch('desktop_platform.ctypes.WinDLL',create=True,side_effect=lambda name:gdi if name=='gdi32' else Mock()):
                panel.round_window(1,width,height,dpi)
            region=regions[0]
            try:
                self.assertFalse(gdi.PtInRegion(region,0,0))
                self.assertFalse(gdi.PtInRegion(region,width-1,height-1))
                self.assertTrue(gdi.PtInRegion(region,width//2,height//2))
            finally:gdi.DeleteObject(region)

    def test_all_taskbar_edges_and_secondary_monitors_stay_in_work_area(self):
        fixtures=[((1800,1040,1824,1080),(0,0,1920,1040),(0,0,1920,1080)),
                  ((1800,0,1824,40),(0,40,1920,1080),(0,0,1920,1080)),
                  ((0,900,40,924),(40,0,1920,1080),(0,0,1920,1080)),
                  ((1880,900,1920,924),(0,0,1880,1080),(0,0,1920,1080)),
                  ((-200,1040,-176,1080),(-1920,0,0,1040),(-1920,0,0,1080)),
                  ((2500,-1000,2524,-976),(1920,-1080,3840,0),(1920,-1080,3840,0))]
        for icon,work,monitor in fixtures:
            for scale in (1,1.25,1.5,1.75,2):
                size=(round(420*scale),round(380*scale))
                with self.subTest(icon=icon,scale=scale):
                    x,y=popup_position(icon,work,monitor,size)
                    self.assertGreaterEqual(x,work[0]+8);self.assertGreaterEqual(y,work[1]+8)
                    self.assertLessEqual(x+size[0],work[2]-8);self.assertLessEqual(y+size[1],work[3]-8)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.panel=NativePanel.__new__(NativePanel)
        self.panel.ready=True;self.panel.visible=False;self.panel.closed=False
        self.panel.busy=False;self.panel.modal=False;self.panel.generation=0
        self.panel.blur_timer=Mock()
        self.panel.blur_generation=None;self.panel.foreground_at_open=None
        self.panel.log=None
        self.panel.signals=Mock();self.panel.manager=Mock();self.panel.manager.preferences.return_value={'autoHide':True}
        self.panel.user=Mock();self.panel.user.GetForegroundWindow.return_value=999
        self.panel.user.GetAncestor.return_value=999
        self.panel.user.GetAsyncKeyState.return_value=0
        self.panel.form=Mock();self.form=Mock();self.form.Handle.ToInt64.return_value=123
        self.panel.form.return_value=self.form;self.panel.place=Mock();self.panel.post=Mock();self.panel.apply_material=Mock()
        self.panel.anchor=(0,0,24,24)
        def cursor(pointer):pointer._obj.x=100;pointer._obj.y=100;return True
        self.panel.user.GetCursorPos.side_effect=cursor
        self.queue=[];self.panel.dispatch=lambda action:self.queue.append(action) or True

    def show_focused(self):
        self.panel.user.GetForegroundWindow.return_value=123
        self.panel.show();self.queue[-1](self.form)

    def test_late_show_and_hide_callbacks_follow_the_latest_click(self):
        self.panel.show();old_show=self.queue[-1]
        self.panel.hide();old_hide=self.queue[-1]
        self.panel.show();new_show=self.queue[-1]
        old_hide(self.form);old_show(self.form);new_show(self.form)
        self.form.Hide.assert_not_called();self.form.Show.assert_called_once()
        self.assertTrue(self.panel.visible)

    def test_hidden_panel_stays_warm_without_an_idle_destruction_timer(self):
        self.panel.hide();self.queue[-1](self.form)
        self.panel.blur_timer.Start.assert_not_called();self.form.Close.assert_not_called()
        self.assertFalse(self.panel.closed)

    def test_late_native_activation_does_not_cancel_a_requested_hide(self):
        self.panel.show();self.queue[-1](self.form)
        self.panel.hide();hide=self.queue[-1]
        self.panel.on_activate();hide(self.form)
        self.form.Hide.assert_called_once()
        self.form.Close.assert_not_called()

    def test_show_sets_topmost_and_hiding_clears_it(self):
        self.panel.show();self.queue[-1](self.form)
        self.assertTrue(self.form.TopMost)
        self.assertEqual(self.panel.user.SetWindowPos.call_args.args[1].value,ctypes.c_void_p(-1).value)
        self.panel.hide();self.queue[-1](self.form)
        self.assertFalse(self.form.TopMost)
        self.panel.signals.set_desired.assert_called_with(False)
        self.assertEqual(self.panel.user.SetWindowPos.call_args.args[1].value,ctypes.c_void_p(-2).value)

    def test_changing_pages_does_not_resize_or_move_the_native_window(self):
        for page in PANEL_SIZES:self.panel.resize(page)
        self.assertEqual(self.panel.page,next(reversed(PANEL_SIZES)));self.assertFalse(self.queue)
        self.panel.place.assert_not_called()

    def test_explicit_exit_cancels_a_queued_show(self):
        self.panel.show();show=self.queue[-1]
        self.panel.close();close=self.queue[-1]
        show(self.form);close(self.form)
        self.form.Show.assert_not_called();self.form.Close.assert_called_once()
        self.assertTrue(self.panel.closed)

    def test_visible_panel_detects_foreground_loss_without_deactivate(self):
        self.show_focused()
        self.panel.blur_timer.Start.assert_called_once()
        self.panel.user.GetForegroundWindow.return_value=999
        self.panel.check_blur(self.form)
        self.assertFalse(self.panel.visible)

    def test_deactivation_checks_before_the_next_poll(self):
        self.show_focused()
        self.panel.user.GetForegroundWindow.return_value=999
        self.panel.on_blur();self.queue[-1](self.form)
        self.assertFalse(self.panel.visible)

    def test_focus_return_keeps_the_foreground_watch_active(self):
        self.show_focused()
        self.panel.blur_timer.reset_mock()
        self.panel.on_blur()
        self.panel.on_activate()
        self.queue[-1](self.form)
        self.panel.blur_timer.Start.assert_called_once()
        self.panel.blur_timer.Stop.assert_not_called()
        self.assertTrue(self.panel.visible)

    def test_opening_without_activation_waits_for_a_foreground_change(self):
        self.form.Activate.side_effect=self.panel.on_activate
        self.panel.show();self.queue[-1](self.form)
        self.panel.check_blur(self.form)
        self.assertTrue(self.panel.visible)
        self.panel.user.GetForegroundWindow.return_value=0
        self.panel.check_blur(self.form)
        self.assertTrue(self.panel.visible)
        self.panel.user.GetForegroundWindow.return_value=1000
        self.panel.check_blur(self.form)
        self.assertFalse(self.panel.visible)

    def test_old_watch_does_not_hide_a_pending_new_show(self):
        self.show_focused()
        self.panel.hide()
        self.panel.show();show=self.queue[-1]
        self.panel.user.GetForegroundWindow.return_value=999
        self.panel.check_blur(self.form)
        show(self.form)
        self.assertTrue(self.panel.visible)
        self.assertEqual(self.panel.blur_generation,self.panel.generation)

    def test_tray_mouse_press_defers_blur_but_hover_does_not(self):
        self.show_focused()
        self.panel.user.GetForegroundWindow.return_value=999
        def cursor(pointer):pointer._obj.x=12;pointer._obj.y=12;return True
        self.panel.user.GetCursorPos.side_effect=cursor
        self.panel.user.GetAsyncKeyState.return_value=-32768
        self.panel.check_blur(self.form)
        self.assertTrue(self.panel.visible)
        self.panel.user.GetAsyncKeyState.return_value=0
        self.panel.check_blur(self.form)
        self.assertFalse(self.panel.visible)

    def test_panel_owned_popup_preserves_focus_until_switching_away(self):
        self.show_focused()
        self.panel.user.GetForegroundWindow.return_value=999
        self.panel.user.GetAncestor.return_value=123
        self.panel.check_blur(self.form)
        self.assertTrue(self.panel.visible)
        self.panel.user.GetAncestor.return_value=999
        self.panel.check_blur(self.form)
        self.assertFalse(self.panel.visible)

    def test_auto_hide_respects_modal_and_in_progress_operations(self):
        for guard in ('busy','modal'):
            self.show_focused();setattr(self.panel,guard,True)
            self.panel.user.GetForegroundWindow.return_value=999
            self.panel.check_blur(self.form)
            self.assertTrue(self.panel.visible);setattr(self.panel,guard,False)
            self.panel.check_blur(self.form)
            self.assertFalse(self.panel.visible)


class PanelHostTests(unittest.TestCase):
    def setUp(self):
        from desktop_app import PanelHost
        self.notify=Mock();self.host=PanelHost(lambda:(1,2,3,4),self.notify)
        self.connector=patch('desktop_app.Signals.connect',return_value=None).start()
        self.launcher=patch('desktop_app.subprocess.Popen').start()
        patch('desktop_app.subprocess.CREATE_NO_WINDOW',new=0x08000000,create=True).start()
        self.child=self.launcher.return_value;self.child.poll.return_value=None
        native_dll=patch('desktop_app.ctypes.windll',new=Mock(),create=True).start()
        self.foreground=native_dll.user32.AllowSetForegroundWindow
        self.addCleanup(patch.stopall)

    def test_startup_prewarms_hidden_and_does_not_request_foreground(self):
        self.host.prepare()
        args=self.launcher.call_args.args[0]
        self.assertIn('--window',args);self.assertNotIn('--show',args)
        self.assertIsNone(self.host.pending);self.foreground.assert_not_called()

    def test_warm_process_receives_the_stable_tray_identity(self):
        self.host.identity=lambda:(0x123456789,7)
        self.host.prepare();args=self.launcher.call_args.args[0]
        self.assertEqual(args[args.index('--tray-window')+1],str(0x123456789))
        self.assertEqual(args[args.index('--tray-id')+1],'7')

    def test_open_and_hide_during_startup_deliver_only_the_latest_intent(self):
        self.host.prepare();self.host.request(True);self.host.request(False)
        self.launcher.assert_called_once()
        sender=Mock();self.connector.return_value=sender
        self.host.poll();sender.signal.assert_called_once_with(hide=True)
        self.assertIsNone(self.host.pending)
        self.host.poll();sender.signal.assert_called_once()

    def test_warm_panel_opens_without_spawning_and_can_be_closed(self):
        self.host.prepare();sender=Mock();self.connector.return_value=sender
        self.host.request(True);self.launcher.assert_called_once()
        sender.signal.assert_called_with(hide=False)
        self.host.close();sender.signal.assert_called_with(close=True)

    def test_toggle_cancels_an_open_that_has_not_become_visible_yet(self):
        sender=Mock();sender.wants_visible.return_value=True;sender.is_visible.return_value=False
        self.connector.return_value=sender
        self.host.toggle();sender.signal.assert_called_with(hide=True)
        sender.wants_visible.return_value=False;sender.is_visible.return_value=True
        self.host.toggle();sender.signal.assert_called_with(hide=False)

    def test_failed_open_notifies_once_and_next_click_can_retry(self):
        self.host.request(True);self.child.poll.return_value=1
        self.host.poll();self.host.poll();self.notify.assert_called_once()
        self.child.poll.return_value=None;self.host.request(True)
        self.assertEqual(self.launcher.call_count,2)


@unittest.skipUnless(os.name=='nt','Windows native events')
class NativeSignalTests(unittest.TestCase):
    def test_senders_never_create_a_false_owner_and_latest_visibility_wins(self):
        from desktop_app import Signals
        role='Test-'+secrets.token_hex(8)
        self.assertIsNone(Signals.connect(role))
        ctypes.set_last_error(183)
        owner=Signals(role);self.assertFalse(owner.existing)
        sender=Signals.connect(role);self.assertIsNotNone(sender)
        try:
            sender.signal();sender.signal(hide=True);sender.signal()
            self.assertEqual(owner.wait(timeout=0),0);self.assertTrue(owner.wants_visible())
            owner.set_visible(True);self.assertTrue(sender.is_visible())
            sender.signal(close=True);self.assertEqual(owner.wait(timeout=0),1)
        finally:sender.dispose();owner.dispose()
        self.assertIsNone(Signals.connect(role))

    def test_taskbar_registration_retries_and_recovers_after_explorer_restart(self):
        from desktop_tray import TrayIcon,ICON_RETRY_TIMER
        icon=TrayIcon.__new__(TrayIcon)
        icon._hwnd=0x123456789;icon._hicon=0x234567890;icon._tip='Rm';icon._uID=1
        icon._shell=Mock();icon._user32=Mock();icon._lock=__import__('threading').Lock()
        icon._log=None;icon._retrying=False
        icon._shell.Shell_NotifyIconW.return_value=False
        self.assertFalse(icon._register_icon('startup'))
        self.assertTrue(icon._retrying);icon._user32.SetTimer.assert_called_once()
        icon._shell.Shell_NotifyIconW.return_value=True
        self.assertTrue(icon._register_icon('taskbar'));self.assertFalse(icon._retrying)
        icon._user32.KillTimer.assert_called_with(icon._hwnd,ICON_RETRY_TIMER)
        data=icon._base_nid();self.assertEqual(data.hWnd,0x123456789)


if __name__=='__main__':unittest.main()
