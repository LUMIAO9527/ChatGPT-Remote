---
name: chatgpt-remote
description: Manage phone connections to this ChatGPT desktop window, or check the local Remote connection.
---

Click Remote directly above Usage at the bottom of the ChatGPT sidebar. The
popover shows connection status and paired devices. Choose 配对手机 to show the
QR code, or choose 已配对设备 to rename or remove a device.

Use `remote_status` for read-only connection checks. Use `remote_service` when
the user requests starting, stopping or restarting the phone connection.

The phone follows the native ChatGPT window the user last activated. Pairing
codes and the QR link stay in the popover.
