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
        with patch('desktop_platform.ctypes.WinDLL',side_effect=lambda name:dwm if name=='dwmapi' else gdi):
            panel.round_window(11,546,802,168)
        gdi.CreateRoundRectRgn.assert_called_once_with(0,0,547,803,56,56)

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
        self.panel.busy=False;self.panel.modal=False;self.panel.generation=0;self.panel.focus_generation=0
        self.panel.log=None
        self.panel.signals=Mock();self.panel.manager=Mock();self.panel.manager.preferences.return_value={'autoHide':True}
        self.panel.user=Mock();self.panel.user.GetForegroundWindow.return_value=999
        self.panel.user.GetAncestor.return_value=999
        self.panel.form=Mock();self.form=Mock();self.form.Handle.ToInt64.return_value=123
        self.panel.form.return_value=self.form;self.panel.place=Mock();self.panel.post=Mock();self.panel.apply_material=Mock()
        self.panel.anchor=(0,0,24,24)
        def cursor(pointer):pointer._obj.x=100;pointer._obj.y=100;return True
        self.panel.user.GetCursorPos.side_effect=cursor
        self.queue=[];self.panel.dispatch=lambda action:self.queue.append(action) or True
        self.timer=patch('desktop_platform.threading.Timer').start();self.addCleanup(patch.stopall)

    def test_late_show_and_hide_callbacks_follow_the_latest_click(self):
        self.panel.show();old_show=self.queue[-1]
        self.panel.hide();old_hide=self.queue[-1]
        self.panel.show();new_show=self.queue[-1]
        old_hide(self.form);old_show(self.form);new_show(self.form)
        self.form.Hide.assert_not_called();self.form.Show.assert_called_once()
        self.assertTrue(self.panel.visible)

    def test_hidden_panel_stays_warm_without_an_idle_destruction_timer(self):
        self.panel.hide();self.queue[-1](self.form)
        self.timer.assert_not_called();self.form.Close.assert_not_called()
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
        self.assertEqual(self.panel.page,'update');self.assertFalse(self.queue)
        self.panel.place.assert_not_called()

    def test_explicit_exit_cancels_a_queued_show(self):
        self.panel.show();show=self.queue[-1]
        self.panel.close();close=self.queue[-1]
        show(self.form);close(self.form)
        self.form.Show.assert_not_called();self.form.Close.assert_called_once()
        self.assertTrue(self.panel.closed)

    def test_focus_return_or_tray_click_cancels_delayed_blur(self):
        self.panel.show();self.panel.on_blur();confirm=self.timer.call_args.args[1]
        self.panel.on_activate();confirm();self.assertTrue(self.panel.visible)
        self.panel.on_blur();confirm=self.timer.call_args.args[1]
        def cursor(pointer):pointer._obj.x=12;pointer._obj.y=12;return True
        self.panel.user.GetCursorPos.side_effect=cursor
        confirm();self.assertTrue(self.panel.visible)

    def test_auto_hide_respects_modal_and_in_progress_operations(self):
        for guard in ('busy','modal'):
            self.panel.show();setattr(self.panel,guard,True)
            self.panel.on_blur();self.timer.call_args.args[1]()
            self.assertTrue(self.panel.visible);setattr(self.panel,guard,False)
        self.panel.on_blur();self.timer.call_args.args[1]()
        self.assertFalse(self.panel.visible)


class PanelHostTests(unittest.TestCase):
    def setUp(self):
        from desktop_app import PanelHost
        self.notify=Mock();self.host=PanelHost(lambda:(1,2,3,4),self.notify)
        self.connector=patch('desktop_app.Signals.connect',return_value=None).start()
        self.launcher=patch('desktop_app.subprocess.Popen').start()
        self.child=self.launcher.return_value;self.child.poll.return_value=None
        self.foreground=patch('desktop_app.ctypes.windll.user32.AllowSetForegroundWindow').start()
        self.addCleanup(patch.stopall)

    def test_startup_prewarms_hidden_and_does_not_request_foreground(self):
        self.host.prepare()
        args=self.launcher.call_args.args[0]
        self.assertIn('--window',args);self.assertNotIn('--show',args)
        self.assertIsNone(self.host.pending);self.foreground.assert_not_called()

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
