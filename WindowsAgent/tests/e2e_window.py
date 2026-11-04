"""Minimal window used only by the optional Windows integration test.

It writes the *actual Entry contents* to a log file after every key event, so
the test can prove that remote Unicode input really reached another process.
(Logging Tk's KeyRelease ``event.char`` would be unreliable: Tk synthesises
extra release events for Unicode SendInput, while the Entry text is correct.)
"""
import argparse
import pathlib
import tkinter as tk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--class-name", default="HarmonyE2EWindow")
    args = parser.parse_args()
    log_path = pathlib.Path(args.log)
    log_path.write_text("", encoding="utf-8")

    root = tk.Tk(className=args.class_name)
    root.title(args.title)
    root.geometry("560x240+120+120")
    root.attributes("-topmost", True)
    label = tk.Label(root, text="Harmony E2E test window — remote input target",
                     font=("Segoe UI", 11))
    label.pack(padx=16, pady=(16, 6))
    entry = tk.Entry(root, font=("Segoe UI", 16))
    entry.pack(fill="x", padx=16, pady=8)
    status = tk.Label(root, text="waiting for remote input", fg="#555")
    status.pack(pady=4)

    def save_contents(*_args):
        text = entry.get()
        log_path.write_text(text, encoding="utf-8")
        status.config(text=f"received {len(text)} chars")

    entry.bind("<KeyPress>", lambda _event: root.after(5, save_contents))
    entry.bind("<KeyRelease>", lambda _event: root.after(5, save_contents))
    entry.focus_force()
    root.bind("<Escape>", lambda _event: root.destroy())
    root.after(90_000, root.destroy)  # safety net; the test kills it earlier
    root.mainloop()


if __name__ == "__main__":
    main()