"""
app.py — WiFi Direct & High-Speed LAN File Transfer Application
Clean, Minimalist, Precision Dark Interface.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import sys
import socket
import logging
import queue
import time
from pathlib import Path
from typing import Optional, List, Dict

# High-DPI display scaling for Windows (e.g. 4K Precision displays)
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Ensure core package is resolvable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.transfer import FileReceiver, send_file, TransferProgress, DEFAULT_PORT
from core.network import (
    get_all_adapters, get_physical_adapters, get_best_transfer_ip,
    format_size, format_speed, format_eta
)
from core.discovery import PeerBeacon, PeerListener, DISCOVERY_PORT

logger = logging.getLogger(__name__)

# ─── Obsidian Black & Cobalt Blue Minimalist Palette ──────────────────────────
BG_OBSIDIAN  = "#08090c"   # Pure deep black base
BG_CARD      = "#0f1218"   # Low-profile flat surface
BG_INPUT     = "#151922"   # Inset well
BORDER       = "#222736"   # Sharp precision border
BORDER_SUBTLE= "#181d28"

ACCENT_BLUE  = "#2563eb"   # Sharp cobalt blue
ACCENT_MUTED = "#1d293d"   # Dark navy container
ACCENT_HOVER = "#1d4ed8"

TEXT_WHITE   = "#ffffff"   # High-contrast primary
TEXT_LIGHT   = "#e2e8f0"   # Clean secondary
TEXT_MUTED   = "#8e9bb0"   # Metadata
TEXT_DIM     = "#475569"   # Timestamps & subtleties

# Functional status colors
SUCCESS      = "#10b981"
WARNING      = "#f59e0b"
ERROR        = "#ef4444"
PEER_CYAN    = "#38bdf8"

FONT_TITLE   = ("Segoe UI", 15, "bold")
FONT_HEADING = ("Segoe UI", 10, "bold")
FONT_BODY    = ("Segoe UI", 10)
FONT_MONO    = ("Consolas", 10)
FONT_SMALL   = ("Segoe UI", 9)
FONT_SPEED   = ("Consolas", 13, "bold")
FONT_CODE    = ("Consolas", 9)


class FlatButton(tk.Button):
    """Clean, stable, non-animated flat button."""
    def __init__(self, master, text, command=None, bg=ACCENT_BLUE, fg=TEXT_WHITE,
                 padx=14, pady=6, font=FONT_BODY, **kwargs):
        super().__init__(
            master, text=text, command=command,
            bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
            relief="flat", bd=0, cursor="hand2",
            padx=padx, pady=pady, font=font, **kwargs
        )
        self._bg = bg

    def set_state(self, enabled: bool):
        self.config(state="normal" if enabled else "disabled")


class StatusDot(tk.Canvas):
    """Minimal solid status indicator."""
    def __init__(self, master, size=10, color=TEXT_DIM, **kwargs):
        super().__init__(master, width=size, height=size, bg=BG_OBSIDIAN,
                         highlightthickness=0, **kwargs)
        self._size = size
        self._dot = self.create_oval(1, 1, size-1, size-1, fill=color, outline="")

    def set_color(self, color: str):
        self.itemconfig(self._dot, fill=color)


class TransferRow(tk.Frame):
    """Clean transfer row for History."""
    def __init__(self, master, filename: str, file_size: int, direction: str, **kwargs):
        super().__init__(master, bg=BG_CARD, pady=8, padx=12,
                         highlightbackground=BORDER_SUBTLE, highlightthickness=1, **kwargs)
        self._direction = direction
        self._total = file_size

        tag_text = "SEND" if direction == "send" else "RECV"
        tag_bg = ACCENT_MUTED if direction == "send" else "#064e3b"
        tag_fg = ACCENT_BLUE if direction == "send" else SUCCESS

        tag_lbl = tk.Label(self, text=f" {tag_text} ", fg=tag_fg, bg=tag_bg,
                           font=("Consolas", 8, "bold"), width=6)
        tag_lbl.pack(side="left", padx=(0, 10))

        info_frame = tk.Frame(self, bg=BG_CARD)
        info_frame.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self.lbl_name = tk.Label(info_frame, text=filename, fg=TEXT_WHITE,
                                  bg=BG_CARD, font=FONT_BODY, anchor="w")
        self.lbl_name.pack(fill="x")

        self.lbl_detail = tk.Label(info_frame, text=format_size(file_size),
                                    fg=TEXT_MUTED, bg=BG_CARD, font=FONT_SMALL, anchor="w")
        self.lbl_detail.pack(fill="x")

        right_frame = tk.Frame(self, bg=BG_CARD, width=250)
        right_frame.pack(side="right")
        right_frame.pack_propagate(False)

        self.progress_bar = ttk.Progressbar(right_frame, length=240, mode="determinate")
        self.progress_bar.pack(pady=(0, 2))

        self.lbl_status = tk.Label(right_frame, text="Starting...", fg=TEXT_MUTED,
                                    bg=BG_CARD, font=FONT_SMALL)
        self.lbl_status.pack()

    def update_progress(self, progress: TransferProgress):
        pct = progress.percent
        self.progress_bar["value"] = pct
        speed = format_speed(progress.speed_mbps)
        eta = format_eta(progress.eta_seconds) if not progress.done else ""

        if progress.error:
            self.lbl_status.config(text=f"Failed: {progress.error[:32]}", fg=ERROR)
        elif progress.done:
            self.lbl_status.config(
                text=f"Verified ({progress.elapsed:.2f}s @ {speed})", fg=SUCCESS
            )
            self.progress_bar["value"] = 100
        else:
            status_text = f"{speed} ({pct:.0f}%)"
            if eta:
                status_text += f" • ETA {eta}"
            self.lbl_status.config(text=status_text, fg=TEXT_WHITE)

        self.lbl_detail.config(
            text=f"{format_size(progress.transferred_bytes)} / {format_size(self._total)}"
        )


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("WiFi File Transfer")
        self.geometry("920x700")
        self.minsize(800, 560)
        self.configure(bg=BG_OBSIDIAN)

        self._setup_styles()

        self._hostname = socket.gethostname()
        self._receiver: Optional[FileReceiver] = None
        self._receiver_running = False
        self._beacon: Optional[PeerBeacon] = None
        self._listener: Optional[PeerListener] = None
        self._discovered_peers: Dict[str, Dict[str, str]] = {}
        self._ui_queue: queue.Queue = queue.Queue()
        self._transfer_rows: Dict[str, TransferRow] = {}

        self._send_files: List[str] = []
        self._current_cancel_event: Optional[threading.Event] = None

        self._build_ui()
        self._poll_ui_queue()

        # Initial logging
        self._log_event("info", f"Initialized WiFi File Transfer on {self._hostname}")

        # Start peer discovery listener
        self._start_peer_listener()

        # Auto-start receiver on launch so both PCs are immediately ready
        self._start_receiver()

        # Scan network adapters
        self._refresh_network()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background=BG_OBSIDIAN, foreground=TEXT_LIGHT, font=FONT_BODY)
        style.configure("TFrame", background=BG_OBSIDIAN)
        style.configure("TLabel", background=BG_OBSIDIAN, foreground=TEXT_LIGHT)
        
        style.configure("TNotebook", background=BG_OBSIDIAN, borderwidth=0)
        style.configure("TNotebook.Tab",
                         background=BG_CARD, foreground=TEXT_MUTED,
                         padding=[20, 8], font=FONT_HEADING, borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BG_INPUT)],
                  foreground=[("selected", TEXT_WHITE)])

        style.configure("Horizontal.TProgressbar",
                         troughcolor=BG_INPUT, background=ACCENT_BLUE,
                         borderwidth=0, thickness=6)

        style.configure("TCombobox",
                         fieldbackground=BG_INPUT, background=BG_CARD,
                         foreground=TEXT_WHITE, borderwidth=1,
                         arrowcolor=TEXT_LIGHT)
        style.map("TCombobox", fieldbackground=[("readonly", BG_INPUT)],
                               selectbackground=[("readonly", BG_INPUT)],
                               selectforeground=[("readonly", TEXT_WHITE)])

        style.configure("TEntry",
                         fieldbackground=BG_INPUT, foreground=TEXT_WHITE,
                         insertcolor=TEXT_WHITE, borderwidth=1, relief="flat")

    def _build_ui(self):
        # ── Header ───────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG_OBSIDIAN, pady=14, padx=22)
        header.pack(fill="x")

        title_frame = tk.Frame(header, bg=BG_OBSIDIAN)
        title_frame.pack(side="left")

        tk.Label(title_frame, text="WIFI DIRECT & LAN TRANSFER",
                 fg=TEXT_WHITE, bg=BG_OBSIDIAN, font=FONT_TITLE).pack(side="left")

        host_pill = tk.Frame(title_frame, bg=BG_INPUT, padx=8, pady=2,
                             highlightbackground=BORDER, highlightthickness=1)
        host_pill.pack(side="left", padx=(12, 0))
        tk.Label(host_pill, text=self._hostname, fg=TEXT_MUTED, bg=BG_INPUT, font=FONT_CODE).pack()

        status_pill = tk.Frame(header, bg=BG_INPUT, padx=10, pady=4,
                               highlightbackground=BORDER, highlightthickness=1)
        status_pill.pack(side="right")

        self._dot = StatusDot(status_pill, size=8, color=SUCCESS)
        self._dot.pack(side="left", padx=(0, 6))

        self._lbl_status_main = tk.Label(
            status_pill, text="RECEIVER ONLINE", fg=SUCCESS, bg=BG_INPUT, font=("Segoe UI", 8, "bold")
        )
        self._lbl_status_main.pack(side="left")

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=0, pady=0)

        # Tab 1: RECEIVE / HOST
        self._tab_host = tk.Frame(self._notebook, bg=BG_OBSIDIAN)
        self._notebook.add(self._tab_host, text="  RECEIVE / HOST  ")
        self._build_host_tab(self._tab_host)

        # Tab 2: SEND
        self._tab_send = tk.Frame(self._notebook, bg=BG_OBSIDIAN)
        self._notebook.add(self._tab_send, text="  SEND FILES  ")
        self._build_send_tab(self._tab_send)

        # Tab 3: HISTORY
        self._tab_transfers = tk.Frame(self._notebook, bg=BG_OBSIDIAN)
        self._notebook.add(self._tab_transfers, text="  HISTORY  ")
        self._build_transfers_tab(self._tab_transfers)

        # ── Status bar ────────────────────────────────────────────────────────
        statusbar = tk.Frame(self, bg=BG_CARD, pady=6, padx=18,
                             highlightbackground=BORDER, highlightthickness=1)
        statusbar.pack(fill="x", side="bottom")

        self._lbl_statusbar = tk.Label(
            statusbar, text="Ready for incoming and outgoing transfers",
            fg=TEXT_MUTED, bg=BG_CARD, font=FONT_SMALL
        )
        self._lbl_statusbar.pack(side="left")

        self._lbl_adapters = tk.Label(
            statusbar, text="Scanning network...", fg=TEXT_DIM, bg=BG_CARD, font=FONT_SMALL
        )
        self._lbl_adapters.pack(side="right")

    # ─── RECEIVE / HOST TAB ───────────────────────────────────────────────────
    def _build_host_tab(self, parent):
        outer = tk.Frame(parent, bg=BG_OBSIDIAN)
        outer.pack(fill="both", expand=True, padx=22, pady=16)

        # Network Info Card
        card_info = tk.Frame(outer, bg=BG_CARD, padx=16, pady=14,
                             highlightbackground=BORDER, highlightthickness=1)
        card_info.pack(fill="x", pady=(0, 12))

        header_row = tk.Frame(card_info, bg=BG_CARD)
        header_row.pack(fill="x", pady=(0, 8))
        tk.Label(header_row, text="LOCAL NETWORK INTERFACES",
                 fg=TEXT_MUTED, bg=BG_CARD, font=FONT_HEADING).pack(side="left")

        # Primary IP Display
        ip_row = tk.Frame(card_info, bg=BG_CARD)
        ip_row.pack(fill="x", pady=2)
        tk.Label(ip_row, text="Primary Wi-Fi IP:", fg=TEXT_LIGHT,
                 bg=BG_CARD, font=FONT_BODY, width=18, anchor="w").pack(side="left")
        self._lbl_host_ip = tk.Label(ip_row, text="Detecting IP...", fg=TEXT_WHITE,
                                      bg=BG_CARD, font=FONT_SPEED)
        self._lbl_host_ip.pack(side="left")

        # All Detected Interfaces list
        self._lbl_adapter_details = tk.Label(
            card_info, text="Scanning adapters...", fg=TEXT_MUTED,
            bg=BG_CARD, font=FONT_CODE, justify="left"
        )
        self._lbl_adapter_details.pack(anchor="w", pady=(6, 0))

        # Destination Folder Card
        card_folder = tk.Frame(outer, bg=BG_CARD, padx=16, pady=12,
                               highlightbackground=BORDER, highlightthickness=1)
        card_folder.pack(fill="x", pady=(0, 12))

        tk.Label(card_folder, text="SAVE DIRECTORY",
                 fg=TEXT_MUTED, bg=BG_CARD, font=FONT_HEADING).pack(anchor="w", pady=(0, 6))
        f_row = tk.Frame(card_folder, bg=BG_CARD)
        f_row.pack(fill="x")

        default_recv = str(Path.home() / "Downloads" / "WiFiTransfer")
        self._recv_dir_var = tk.StringVar(value=default_recv)
        ttk.Entry(f_row, textvariable=self._recv_dir_var,
                  font=FONT_MONO).pack(side="left", fill="x", expand=True)

        FlatButton(f_row, text="Browse...", command=self._browse_recv_dir,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=12, pady=4).pack(side="left", padx=(8, 0))

        # Control Buttons
        btn_row = tk.Frame(outer, bg=BG_OBSIDIAN)
        btn_row.pack(fill="x", pady=(0, 12))

        self._btn_start_receiver = FlatButton(
            btn_row, text="■  Stop Receiver",
            command=self._toggle_receiver,
            bg=ERROR, fg=TEXT_WHITE
        )
        self._btn_start_receiver.pack(side="left")

        FlatButton(
            btn_row, text="Mobile Hotspot Settings",
            command=self._open_hotspot_settings,
            bg=BG_INPUT, fg=TEXT_LIGHT
        ).pack(side="left", padx=(8, 0))

        FlatButton(
            btn_row, text="Wi-Fi Settings",
            command=self._open_wifi_settings,
            bg=BG_INPUT, fg=TEXT_LIGHT
        ).pack(side="left", padx=(8, 0))

        FlatButton(
            btn_row, text="Fix Firewall",
            command=self._run_firewall_fix,
            bg=BG_INPUT, fg=TEXT_LIGHT
        ).pack(side="left", padx=(8, 0))

        FlatButton(
            btn_row, text="⟳ Refresh",
            command=self._refresh_network,
            bg=BG_INPUT, fg=TEXT_LIGHT, padx=10
        ).pack(side="left", padx=(8, 0))

        # Live Activity Log
        log_card = tk.Frame(outer, bg=BG_CARD, padx=14, pady=10,
                            highlightbackground=BORDER, highlightthickness=1)
        log_card.pack(fill="both", expand=True)

        log_hdr = tk.Frame(log_card, bg=BG_CARD)
        log_hdr.pack(fill="x", pady=(0, 6))
        tk.Label(log_hdr, text="ACTIVITY & HANDSHAKE LOG", fg=TEXT_MUTED,
                 bg=BG_CARD, font=FONT_HEADING).pack(side="left")
        FlatButton(log_hdr, text="Clear Log", command=self._clear_log,
                   bg=BG_INPUT, fg=TEXT_MUTED, padx=8, pady=2, font=FONT_SMALL).pack(side="right")

        log_inner = tk.Frame(log_card, bg=BG_OBSIDIAN, highlightbackground=BORDER, highlightthickness=1)
        log_inner.pack(fill="both", expand=True)

        self._host_log = tk.Text(
            log_inner, bg=BG_OBSIDIAN, fg=TEXT_LIGHT, font=FONT_CODE,
            wrap="word", relief="flat", padx=10, pady=8
        )
        scrollbar = ttk.Scrollbar(log_inner, orient="vertical", command=self._host_log.yview)
        self._host_log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._host_log.pack(side="left", fill="both", expand=True)

        # Configure Color Tags
        self._host_log.tag_config("timestamp", foreground=TEXT_DIM)
        self._host_log.tag_config("info", foreground=TEXT_MUTED)
        self._host_log.tag_config("peer", foreground=PEER_CYAN)
        self._host_log.tag_config("handshake", foreground=WARNING)
        self._host_log.tag_config("send", foreground=ACCENT_BLUE)
        self._host_log.tag_config("recv", foreground=SUCCESS)
        self._host_log.tag_config("done", foreground=SUCCESS)
        self._host_log.tag_config("error", foreground=ERROR)

        self._host_log.bind("<Key>", lambda e: "break")

    # ─── SEND TAB ─────────────────────────────────────────────────────────────
    def _build_send_tab(self, parent):
        outer = tk.Frame(parent, bg=BG_OBSIDIAN)
        outer.pack(fill="both", expand=True, padx=22, pady=16)

        # Receiver Target Card (with Auto-Discovery)
        card_target = tk.Frame(outer, bg=BG_CARD, padx=16, pady=12,
                               highlightbackground=BORDER, highlightthickness=1)
        card_target.pack(fill="x", pady=(0, 12))

        tk.Label(card_target, text="TARGET RECEIVER PC",
                 fg=TEXT_MUTED, bg=BG_CARD, font=FONT_HEADING).pack(anchor="w", pady=(0, 6))

        # Discovered peers row
        peer_row = tk.Frame(card_target, bg=BG_CARD)
        peer_row.pack(fill="x", pady=(0, 8))

        tk.Label(peer_row, text="Discovered Devices:", fg=TEXT_LIGHT,
                 bg=BG_CARD, font=FONT_BODY, width=16, anchor="w").pack(side="left")

        self._peer_combo_var = tk.StringVar()
        self._peer_combo = ttk.Combobox(
            peer_row, textvariable=self._peer_combo_var,
            state="readonly", font=FONT_BODY
        )
        self._peer_combo.pack(side="left", fill="x", expand=True)
        self._peer_combo.bind("<<ComboboxSelected>>", self._on_peer_selected)

        FlatButton(peer_row, text="⟳ Rescan", command=self._rescan_peers,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=10, pady=4).pack(side="left", padx=(8, 0))

        # Manual IP input row
        ip_row = tk.Frame(card_target, bg=BG_CARD)
        ip_row.pack(fill="x")

        tk.Label(ip_row, text="Or Target IP:", fg=TEXT_LIGHT,
                 bg=BG_CARD, font=FONT_BODY, width=16, anchor="w").pack(side="left")

        self._target_ip_var = tk.StringVar(value="")
        ttk.Entry(ip_row, textvariable=self._target_ip_var,
                  font=FONT_MONO, width=20).pack(side="left")

        tk.Label(ip_row, text=" : ", fg=TEXT_MUTED, bg=BG_CARD, font=FONT_MONO).pack(side="left")
        self._port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(ip_row, textvariable=self._port_var,
                  font=FONT_MONO, width=8).pack(side="left")

        # Files Card
        card_files = tk.Frame(outer, bg=BG_CARD, padx=16, pady=12,
                              highlightbackground=BORDER, highlightthickness=1)
        card_files.pack(fill="both", expand=True, pady=(0, 12))

        file_header = tk.Frame(card_files, bg=BG_CARD)
        file_header.pack(fill="x", pady=(0, 6))

        tk.Label(file_header, text="FILES TO TRANSFER",
                 fg=TEXT_MUTED, bg=BG_CARD, font=FONT_HEADING).pack(side="left")

        self._lbl_total_size = tk.Label(file_header, text="0 files (0 B)",
                                         fg=TEXT_WHITE, bg=BG_CARD, font=FONT_CODE)
        self._lbl_total_size.pack(side="right")

        list_frame = tk.Frame(card_files, bg=BG_OBSIDIAN, highlightbackground=BORDER, highlightthickness=1)
        list_frame.pack(fill="both", expand=True, pady=(0, 8))

        self._file_listbox = tk.Listbox(
            list_frame, bg=BG_OBSIDIAN, fg=TEXT_LIGHT, font=FONT_CODE,
            selectbackground=ACCENT_BLUE, selectforeground=TEXT_WHITE,
            relief="flat", height=5, selectmode="extended"
        )
        lb_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self._file_listbox.yview)
        self._file_listbox.configure(yscrollcommand=lb_scroll.set)
        lb_scroll.pack(side="right", fill="y")
        self._file_listbox.pack(side="left", fill="both", expand=True)

        btn_files_row = tk.Frame(card_files, bg=BG_CARD)
        btn_files_row.pack(fill="x")

        FlatButton(btn_files_row, text="+ Add Files...", command=self._add_files,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=12, pady=4).pack(side="left")
        FlatButton(btn_files_row, text="Remove", command=self._remove_files,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=10, pady=4).pack(side="left", padx=(6, 0))
        FlatButton(btn_files_row, text="Clear", command=self._clear_files,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=10, pady=4).pack(side="left", padx=(6, 0))

        # Transfer Progress Card
        card_prog = tk.Frame(outer, bg=BG_CARD, padx=16, pady=12,
                             highlightbackground=BORDER, highlightthickness=1)
        card_prog.pack(fill="x", pady=(0, 12))

        prog_header = tk.Frame(card_prog, bg=BG_CARD)
        prog_header.pack(fill="x", pady=(0, 4))
        tk.Label(prog_header, text="TRANSFER STATUS", fg=TEXT_MUTED,
                 bg=BG_CARD, font=FONT_HEADING).pack(side="left")
        self._lbl_send_rate = tk.Label(prog_header, text="Ready", fg=TEXT_WHITE,
                                        bg=BG_CARD, font=FONT_CODE)
        self._lbl_send_rate.pack(side="right")

        self._send_progress = ttk.Progressbar(card_prog, mode="determinate", length=500)
        self._send_progress.pack(fill="x")

        # Action Buttons
        send_action_row = tk.Frame(outer, bg=BG_OBSIDIAN)
        send_action_row.pack(fill="x")

        self._btn_send = FlatButton(
            send_action_row, text="START TRANSFER",
            command=self._start_send,
            bg=ACCENT_BLUE, fg=TEXT_WHITE, padx=24, pady=8, font=FONT_HEADING
        )
        self._btn_send.pack(side="left")

        self._btn_cancel_send = FlatButton(
            send_action_row, text="Cancel",
            command=self._cancel_send,
            bg=BG_INPUT, fg=ERROR, padx=14, pady=8
        )
        self._btn_cancel_send.pack(side="left", padx=(8, 0))
        self._btn_cancel_send.set_state(False)

    # ─── HISTORY TAB ──────────────────────────────────────────────────────────
    def _build_transfers_tab(self, parent):
        header_row = tk.Frame(parent, bg=BG_OBSIDIAN, pady=10, padx=22)
        header_row.pack(fill="x")

        tk.Label(header_row, text="ALL TRANSFERS",
                 fg=TEXT_MUTED, bg=BG_OBSIDIAN, font=FONT_HEADING).pack(side="left")

        FlatButton(header_row, text="Clear Finished", command=self._clear_history,
                   bg=BG_INPUT, fg=TEXT_LIGHT, padx=12, pady=4).pack(side="right")

        canvas_frame = tk.Frame(parent, bg=BG_OBSIDIAN)
        canvas_frame.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        self._history_canvas = tk.Canvas(canvas_frame, bg=BG_OBSIDIAN, highlightthickness=0)
        history_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._history_canvas.yview)
        self._history_canvas.configure(yscrollcommand=history_scroll.set)
        history_scroll.pack(side="right", fill="y")
        self._history_canvas.pack(side="left", fill="both", expand=True)

        self._history_inner = tk.Frame(self._history_canvas, bg=BG_OBSIDIAN)
        self._history_window = self._history_canvas.create_window(
            (0, 0), window=self._history_inner, anchor="nw"
        )
        self._history_inner.bind(
            "<Configure>",
            lambda e: self._history_canvas.configure(scrollregion=self._history_canvas.bbox("all"))
        )
        self._history_canvas.bind(
            "<Configure>",
            lambda e: self._history_canvas.itemconfig(self._history_window, width=e.width)
        )

        self._lbl_no_history = tk.Label(
            self._history_inner, text="No transfers yet. Files will appear here in real-time.",
            fg=TEXT_DIM, bg=BG_OBSIDIAN, font=FONT_BODY, pady=40
        )
        self._lbl_no_history.pack()

    # ─── SETTINGS & SYSTEM NAVIGATION ─────────────────────────────────────────
    def _open_hotspot_settings(self):
        try:
            os.startfile("ms-settings:network-mobilehotspot")
            self._log_event("info", "Opened Windows Mobile Hotspot settings.")
        except Exception:
            try:
                os.startfile("ms-settings:network")
            except Exception as e:
                messagebox.showerror("Settings", f"Could not open Windows Settings: {e}")

    def _open_wifi_settings(self):
        try:
            os.startfile("ms-settings:network-wifi")
            self._log_event("info", "Opened Windows Wi-Fi settings.")
        except Exception:
            try:
                os.startfile("ms-settings:network")
            except Exception as e:
                messagebox.showerror("Settings", f"Could not open Windows Settings: {e}")

    def _run_firewall_fix(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "allow_firewall.bat")
        script = os.path.abspath(script)
        if os.path.exists(script):
            try:
                os.startfile(script)
                self._log_event("info", "Executed Windows Firewall 1-Click fix.")
            except Exception as e:
                messagebox.showerror("Firewall", f"Could not run script: {e}")
        else:
            messagebox.showwarning("File missing", "allow_firewall.bat not found.")

    # ─── RECEIVER CONTROL ─────────────────────────────────────────────────────
    def _toggle_receiver(self):
        if self._receiver_running:
            self._stop_receiver()
        else:
            self._start_receiver()

    def _start_receiver(self):
        if self._receiver_running:
            return

        save_dir = self._recv_dir_var.get().strip() or str(Path.home() / "Downloads" / "WiFiTransfer")
        self._receiver = FileReceiver(save_dir=save_dir, port=DEFAULT_PORT)
        self._receiver.start(
            progress_callback=lambda p: self._ui_queue.put(("recv_progress", p)),
            done_callback=lambda p: self._ui_queue.put(("recv_done", p)),
            status_callback=lambda tag, msg: self._ui_queue.put(("log_event", tag, msg)),
        )
        self._receiver_running = True

        if not self._beacon:
            self._beacon = PeerBeacon(device_name=self._hostname, transfer_port=DEFAULT_PORT)
            self._beacon.start()

        self._btn_start_receiver.config(text="■  Stop Receiver", bg=ERROR)
        self._dot.set_color(SUCCESS)
        self._lbl_status_main.config(text="RECEIVER ONLINE", fg=SUCCESS)
        self._log_event("info", f"Receiver listening on port {DEFAULT_PORT}. Target folder: {save_dir}")

    def _stop_receiver(self):
        if self._receiver:
            self._receiver.stop()
            self._receiver = None
        if self._beacon:
            self._beacon.stop()
            self._beacon = None

        self._receiver_running = False
        self._btn_start_receiver.config(text="▶  Start Receiver", bg=SUCCESS)
        self._dot.set_color(TEXT_DIM)
        self._lbl_status_main.config(text="RECEIVER IDLE", fg=TEXT_MUTED)
        self._log_event("info", "Receiver stopped.")

    # ─── SEND LOGIC ───────────────────────────────────────────────────────────
    def _start_send(self):
        if not self._send_files:
            messagebox.showinfo("Select Files", "Please add at least one file to send.")
            return

        host = self._target_ip_var.get().strip()
        if not host:
            messagebox.showwarning("No Target IP", "Enter the target PC IP address or select a discovered device.")
            return

        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            port = DEFAULT_PORT

        self._btn_send.set_state(False)
        self._btn_cancel_send.set_state(True)
        self._lbl_send_rate.config(text=f"Connecting to {host}:{port}...", fg=WARNING)
        self._dot.set_color(ACCENT_BLUE)
        self._lbl_status_main.config(text="TRANSFERRING", fg=ACCENT_BLUE)

        self._current_cancel_event = threading.Event()
        cancel_ev = self._current_cancel_event
        files_to_send = list(self._send_files)

        def _send_worker():
            for filepath in files_to_send:
                if cancel_ev.is_set():
                    break

                fname = Path(filepath).name
                fsize = Path(filepath).stat().st_size

                self._ui_queue.put(("new_send_row", fname, fsize))

                progress = send_file(
                    host=host,
                    filepath=filepath,
                    port=port,
                    progress_callback=lambda p: self._ui_queue.put(("send_progress", p)),
                    status_callback=lambda tag, msg: self._ui_queue.put(("log_event", tag, msg)),
                    cancel_event=cancel_ev,
                )

                self._ui_queue.put(("send_done", progress))
                if progress.error:
                    break

            self._ui_queue.put(("send_all_finished",))

        threading.Thread(target=_send_worker, daemon=True).start()

    def _cancel_send(self):
        if self._current_cancel_event:
            self._current_cancel_event.set()
            self._log_event("info", "Transfer cancellation requested.")

    def _add_files(self):
        files = filedialog.askopenfilenames(title="Select files to send")
        for f in files:
            if f not in self._send_files:
                self._send_files.append(f)
                self._file_listbox.insert("end", Path(f).name)
        self._update_file_summary()

    def _remove_files(self):
        selected = list(self._file_listbox.curselection())
        for idx in reversed(selected):
            self._file_listbox.delete(idx)
            del self._send_files[idx]
        self._update_file_summary()

    def _clear_files(self):
        self._file_listbox.delete(0, "end")
        self._send_files.clear()
        self._update_file_summary()

    def _update_file_summary(self):
        count = len(self._send_files)
        total_bytes = sum(Path(f).stat().st_size for f in self._send_files if os.path.exists(f))
        self._lbl_total_size.config(text=f"{count} file{'s' if count != 1 else ''} ({format_size(total_bytes)})")

    # ─── DISCOVERY & NETWORKING ───────────────────────────────────────────────
    def _start_peer_listener(self):
        self._listener = PeerListener(
            on_peer_found=lambda info: self._ui_queue.put(("peer_discovered", info))
        )
        self._listener.start()

    def _rescan_peers(self):
        self._discovered_peers.clear()
        self._peer_combo["values"] = []
        self._peer_combo_var.set("")
        self._lbl_statusbar.config(text="Scanning network for WiFi Transfer peers...")
        self._log_event("info", "Rescanning network for peer devices...")

    def _on_peer_selected(self, event=None):
        selected_text = self._peer_combo_var.get()
        if not selected_text:
            return
        for ip, info in self._discovered_peers.items():
            display = f"{info['name']} ({ip})"
            if display == selected_text:
                self._target_ip_var.set(ip)
                self._port_var.set(info.get("port", str(DEFAULT_PORT)))
                self._lbl_statusbar.config(text=f"Selected peer: {info['name']} ({ip})")
                self._log_event("info", f"Target set to peer: {info['name']} ({ip}:{info.get('port', DEFAULT_PORT)})")
                break

    def _browse_recv_dir(self):
        d = filedialog.askdirectory(title="Choose download folder", initialdir=self._recv_dir_var.get())
        if d:
            self._recv_dir_var.set(d)
            self._log_event("info", f"Save folder updated to: {d}")

    def _refresh_network(self):
        threading.Thread(target=self._scan_adapters_worker, daemon=True).start()

    def _scan_adapters_worker(self):
        adapters = get_all_adapters()
        phys = get_physical_adapters()

        best_ip = phys[0]["ip"] if phys else (adapters[0]["ip"] if adapters else "Not connected")
        self._ui_queue.put(("primary_ip", best_ip))

        lines = []
        for a in adapters:
            tag = "[Wi-Fi]" if a["type"] == "wifi" else (
                "[Mobile Hotspot]" if a["type"] == "hotspot" else (
                    "[Ethernet]" if a["type"] == "ethernet" else "[Virtual / WSL]"
                )
            )
            rec = " (Recommended)" if a["ip"] == best_ip else ""
            lines.append(f"  • {a['ip']:16s}  {tag:18s}  {a['name'][:35]}{rec}")

        details = "\n".join(lines) if lines else "No active network adapters found"
        self._ui_queue.put(("adapter_details", details))

        parts = []
        for a in phys:
            icon = "📡" if a["type"] == "hotspot" else ("🔷" if a["type"] == "wifi" else "🔌")
            parts.append(f"{icon} {a['ip']}")
        bar_text = "  |  ".join(parts) if parts else "No physical Wi-Fi/LAN connection"
        self._ui_queue.put(("statusbar_adapters", bar_text))

    def _clear_history(self):
        for widget in self._history_inner.winfo_children():
            widget.destroy()
        self._transfer_rows.clear()
        self._lbl_no_history = tk.Label(
            self._history_inner, text="No transfers yet. Files will appear here in real-time.",
            fg=TEXT_DIM, bg=BG_OBSIDIAN, font=FONT_BODY, pady=40
        )
        self._lbl_no_history.pack()

    def _clear_log(self):
        self._host_log.delete("1.0", "end")
        self._log_event("info", "Log cleared.")

    # ─── UI QUEUE & EVENT DISPATCHER ──────────────────────────────────────────
    def _poll_ui_queue(self):
        try:
            while True:
                item = self._ui_queue.get_nowait()
                self._dispatch_event(item)
        except queue.Empty:
            pass
        self.after(40, self._poll_ui_queue)

    def _dispatch_event(self, item):
        tag = item[0]

        if tag == "log_event":
            _, log_type, msg = item
            self._log_event(log_type, msg)

        elif tag == "primary_ip":
            self._lbl_host_ip.config(text=item[1])
            if not self._target_ip_var.get() and item[1] != "Not connected":
                self._target_ip_var.set(item[1])

        elif tag == "adapter_details":
            self._lbl_adapter_details.config(text=item[1])

        elif tag == "statusbar_adapters":
            self._lbl_adapters.config(text=item[1])

        elif tag == "peer_discovered":
            info = item[1]
            ip = info["ip"]
            is_new = ip not in self._discovered_peers
            self._discovered_peers[ip] = info
            values = [f"{p['name']} ({p['ip']})" for p in self._discovered_peers.values()]
            self._peer_combo["values"] = values
            if not self._peer_combo_var.get() and values:
                self._peer_combo_var.set(values[0])
                self._target_ip_var.set(ip)
                self._port_var.set(info.get("port", str(DEFAULT_PORT)))
            if is_new:
                self._log_event("peer", f"Discovered peer device: {info['name']} ({ip}:{info.get('port', DEFAULT_PORT)})")
            self._lbl_statusbar.config(text=f"Discovered peer PC: {info['name']} ({ip})")

        elif tag == "new_send_row":
            _, fname, fsize = item
            key = f"send_{fname}_{time.time()}"
            self._add_history_row(fname, fsize, "send", key)

        elif tag == "send_progress":
            progress: TransferProgress = item[1]
            self._send_progress["value"] = progress.percent
            speed = format_speed(progress.speed_mbps)
            eta = format_eta(progress.eta_seconds)
            self._lbl_send_rate.config(
                text=f"{progress.percent:.0f}%  •  {speed}  •  ETA {eta}",
                fg=TEXT_WHITE
            )
            if hasattr(self, "_active_send_key") and self._active_send_key in self._transfer_rows:
                self._transfer_rows[self._active_send_key].update_progress(progress)

        elif tag == "send_done":
            progress: TransferProgress = item[1]
            if hasattr(self, "_active_send_key") and self._active_send_key in self._transfer_rows:
                self._transfer_rows[self._active_send_key].update_progress(progress)

            if progress.error:
                self._lbl_send_rate.config(text=f"Failed: {progress.error}", fg=ERROR)
                self._dot.set_color(ERROR)
            else:
                self._lbl_send_rate.config(
                    text=f"Completed in {progress.elapsed:.2f}s ({format_speed(progress.speed_mbps)})",
                    fg=SUCCESS
                )

        elif tag == "send_all_finished":
            self._btn_send.set_state(True)
            self._btn_cancel_send.set_state(False)
            self._dot.set_color(SUCCESS if self._receiver_running else TEXT_DIM)
            self._lbl_status_main.config(
                text="RECEIVER ONLINE" if self._receiver_running else "READY",
                fg=SUCCESS if self._receiver_running else TEXT_MUTED
            )

        elif tag == "recv_progress":
            progress: TransferProgress = item[1]
            key = f"recv_{progress.filename}"
            if key not in self._transfer_rows:
                self._add_history_row(progress.filename, progress.total_bytes, "receive", key)
            self._transfer_rows[key].update_progress(progress)
            self._lbl_statusbar.config(
                text=f"Receiving {progress.filename} — {progress.percent:.0f}% @ {format_speed(progress.speed_mbps)}"
            )

        elif tag == "recv_done":
            progress: TransferProgress = item[1]
            key = f"recv_{progress.filename}"
            if key in self._transfer_rows:
                self._transfer_rows[key].update_progress(progress)
            else:
                self._add_history_row(progress.filename, progress.total_bytes, "receive", key, progress)
            self._lbl_statusbar.config(
                text=f"Saved {progress.filename} ({format_speed(progress.speed_mbps)})" if not progress.error
                else f"Receive error: {progress.error}"
            )

    def _add_history_row(self, filename: str, file_size: int, direction: str,
                         key: str, initial_progress: Optional[TransferProgress] = None):
        if self._lbl_no_history and self._lbl_no_history.winfo_exists():
            self._lbl_no_history.destroy()

        row = TransferRow(self._history_inner, filename, file_size, direction)
        row.pack(fill="x", pady=2, padx=4)
        self._transfer_rows[key] = row
        if direction == "send":
            self._active_send_key = key

        if initial_progress:
            row.update_progress(initial_progress)

    def _log_event(self, tag_type: str, msg: str):
        """Thread-safe colorized logging to the Live Activity Log."""
        ts = time.strftime("%H:%M:%S")
        prefix_map = {
            "info": "[INFO]",
            "peer": "[PEER]",
            "handshake": "[HANDSHAKE]",
            "send": "[SEND]",
            "recv": "[RECV]",
            "done": "[DONE]",
            "error": "[ERROR]",
        }
        tag_label = prefix_map.get(tag_type, "[INFO]")

        self._host_log.insert("end", f"[{ts}] ", "timestamp")
        self._host_log.insert("end", f"{tag_label:11s} ", tag_type)
        self._host_log.insert("end", f"{msg}\n", tag_type)
        self._host_log.see("end")

    def on_close(self):
        self._stop_receiver()
        if self._listener:
            self._listener.stop()
        self.destroy()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
