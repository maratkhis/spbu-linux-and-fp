#!/usr/bin/env python3
import json
import threading
import time
import os
import sys
import queue
import subprocess
from pathlib import Path
from datetime import datetime

from scapy.all import sniff, IP, TCP, UDP
import tkinter as tk
from tkinter import ttk, messagebox

blocked = {}
whitelist = set()
global_lock_until = 0
_whitelist_file = None

def _now() -> float:
    return time.time()

def load_whitelist(path: str):
    global _whitelist_file
    _whitelist_file = Path(path)
    if not _whitelist_file.exists():
        return
    try:
        with _whitelist_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ip = obj.get("ip")
                if ip and obj.get("cmd") == "whitelist":
                    whitelist.add(ip)
                if ip and obj.get("cmd") == "unwhitelist" and ip in whitelist:
                    whitelist.discard(ip)
    except Exception:
        pass

def _append_whitelist_file(cmd: dict):
    if not _whitelist_file:
        return
    try:
        _whitelist_file.parent.mkdir(parents=True, exist_ok=True)
        with _whitelist_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(cmd, ensure_ascii=False) + "\n")
    except Exception:
        pass

def add_whitelist(ip: str) -> bool:
    if not ip:
        return False
    whitelist.add(ip)
    _append_whitelist_file({"cmd": "whitelist", "ip": ip, "time": int(_now())})
    return True

def remove_whitelist(ip: str) -> bool:
    if not ip:
        return False
    whitelist.discard(ip)
    _append_whitelist_file({"cmd": "unwhitelist", "ip": ip, "time": int(_now())})
    return True

def is_whitelisted(ip: str) -> bool:
    return ip in whitelist

def set_global_lockdown(duration: int):
    global global_lock_until
    end = _now() + max(0, int(duration))
    if end > global_lock_until:
        global_lock_until = end

def is_global_locked() -> bool:
    global global_lock_until
    if global_lock_until <= 0:
        return False
    if _now() > global_lock_until:
        global_lock_until = 0
        return False
    return True

def block_ip_with_iptables(ip: str) -> bool:
    """Реальная блокировка через iptables"""
    try:
        # Блокируем входящие пакеты от IP
        subprocess.run([
            'iptables', '-I', 'INPUT', '-s', ip, '-j', 'DROP'
        ], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False

def unblock_ip_with_iptables(ip: str) -> bool:
    """Разблокировка через iptables"""
    try:
        subprocess.run([
            'iptables', '-D', 'INPUT', '-s', ip, '-j', 'DROP'
        ], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False

def block_ip(ip: str, duration: int = 60) -> bool:
    if not ip:
        return False
    if is_whitelisted(ip):
        return False
    
    # Реальная блокировка через iptables
    if block_ip_with_iptables(ip):
        expire = None if not duration or duration <= 0 else int(_now() + int(duration))
        blocked[ip] = expire
        return True
    return False

def unblock_ip(ip: str) -> bool:
    """Полная разблокировка IP"""
    if ip in blocked:
        unblock_ip_with_iptables(ip)
        del blocked[ip]
        return True
    return False

def is_blocked(ip: str) -> bool:
    if is_whitelisted(ip):
        return False
    if is_global_locked():
        return True
    t = blocked.get(ip)
    if not t:
        return False
    if t is None:
        return True
    if _now() > t:
        unblock_ip(ip)
        return False
    return True

def cleanup_iptables():
    """Очистка всех правил, созданных программой"""
    try:
        result = subprocess.run([
            'iptables', '-L', 'INPUT', '--line-numbers'
        ], capture_output=True, text=True, check=True)
        
        lines = result.stdout.split('\n')
        for line in lines:
            if 'DDOS-BLOCK' in line:
                line_num = line.split()[0]
                subprocess.run([
                    'iptables', '-D', 'INPUT', line_num
                ], check=True)
    except Exception:
        pass

rules_state = {
    "counts": {},
    "ports_common": {80, 443, 53, 123, 22, 25, 110, 143, 587, 993, 995},
}

def rule_high_packet_rate(pkt, st):
    if IP not in pkt:
        return False, ""
    s = pkt[IP].src
    st["counts"].setdefault(s, 0)
    st["counts"][s] += 1
    return st["counts"][s] % 20 == 0, "high_rate"

def rule_unusual_port(pkt, st):
    if TCP in pkt:
        d = pkt[TCP].dport
    elif UDP in pkt:
        d = pkt[UDP].dport
    else:
        return False, ""
    return d not in st["ports_common"], f"port_{d}"

arrival_history = {}
last_seen_ts = {}
detector_settings = {}
jsonl_path = None
log_queue = queue.Queue()

def reset_detection_state():
    arrival_history.clear()
    last_seen_ts.clear()
    rules_state["counts"].clear()

def record_arrival(src: str):
    t = time.time()
    arrival_history.setdefault(src, []).append(t)
    last_seen_ts[src] = t

def count_recent(src: str, window: int) -> int:
    t = time.time()
    xs = [x for x in arrival_history.get(src, []) if t - x <= window]
    arrival_history[src] = xs
    return len(xs)

def unique_sources_in_window(window_sec: float) -> int:
    t = time.time()
    return sum(1 for ts in last_seen_ts.values() if t - ts <= window_sec)

def _push_log_event(ev: dict):
    line = json.dumps(ev, ensure_ascii=False)
    print(line, flush=True)
    if jsonl_path:
        try:
            os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    try:
        log_queue.put_nowait(line)
    except Exception:
        pass

def emit_event(pkt, reason: str, extra: dict | None = None):
    ip_layer = pkt[IP]
    ev = {
        "time": datetime.utcnow().isoformat() + "Z",
        "src": ip_layer.src,
        "dst": ip_layer.dst,
        "proto": ip_layer.proto,
        "length": len(pkt),
        "reason": reason,
    }
    if extra:
        ev.update(extra)
    _push_log_event(ev)

def emit_meta(reason: str, extra: dict | None = None):
    ev = {"time": datetime.utcnow().isoformat() + "Z", "reason": reason}
    if extra:
        ev.update(extra)
    _push_log_event(ev)

def handle_packet(pkt):
    if IP not in pkt:
        return
    if not detector_settings:
        return

    src = pkt[IP].src

    if is_global_locked() and not is_whitelisted(src):
        emit_meta("lockdown_drop", {"src": src})
        return

    if is_blocked(src):
        emit_meta("blocked_drop", {"src": src})
        return

    trig1, reason1 = rule_high_packet_rate(pkt, rules_state)
    trig2, reason2 = rule_unusual_port(pkt, rules_state)
    triggered = []
    if trig1:
        triggered.append(reason1)
    if trig2:
        triggered.append(reason2)

    if triggered:
        emit_event(pkt, "+".join(triggered))

    record_arrival(src)

    if detector_settings.get("auto_block"):
        window = detector_settings.get("block_window", 30)
        thr = detector_settings.get("block_threshold", 10)
        dur = detector_settings.get("block_duration", 60)
        if count_recent(src, window) >= thr:
            if block_ip(src, dur):
                emit_meta(
                    "auto_block",
                    {"src": src, "window": window, "threshold": thr, "duration": dur},
                )

    ddos_unique_threshold = 15
    ddos_window_sec = 5.0
    ddos_duration = 10
    if unique_sources_in_window(ddos_window_sec) >= ddos_unique_threshold:
        set_global_lockdown(ddos_duration)
        emit_meta(
            "ddos_lockdown",
            {
                "unique_sources": ddos_unique_threshold,
                "window_sec": ddos_window_sec,
                "lockdown_sec": ddos_duration,
            },
        )

def run_sniffer(stop_event: threading.Event, iface: str, bpf: str):
    while not stop_event.is_set():
        try:
            sniff(
                iface=iface,
                filter=bpf,
                prn=handle_packet,
                store=False,
                timeout=1,
            )
        except Exception as e:
            emit_meta("sniff_error", {"error": str(e)})
            time.sleep(1)

class DetectorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Network Security Monitor")
        self.root.geometry("1000x700")
        
        self.style = ttk.Style()
        self.style.configure("Card.TFrame", background="white", relief="raised", borderwidth=1)
        self.style.configure("Title.TLabel", font=("Arial", 11, "bold"))
        self.style.configure("Status.TLabel", font=("Arial", 10))

        self.app_dir = Path(__file__).resolve().parent
        self.logs_dir = self.app_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        self.jsonl_path = self.logs_dir / "detections.jsonl"
        self.whitelist_path = self.logs_dir / "whitelist.jsonl"
        self.whitelist_path.touch(exist_ok=True)
        load_whitelist(str(self.whitelist_path))

        self.worker_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.seen_ips: set[str] = set()

        self._build_ui()
        self.root.after(200, self._poll_log_queue)
        self.root.after(1000, self._update_ip_status)
        
        # Очистка при выходе
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        """Очистка iptables при закрытии программы"""
        cleanup_iptables()
        self.root.destroy()

    def _build_ui(self):
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill="both", expand=True)

        status_frame = ttk.LabelFrame(main_container, text="Статус системы", padding="10")
        status_frame.pack(fill="x", pady=(0, 10))

        status_grid = ttk.Frame(status_frame)
        status_grid.pack(fill="x")

        self.status_var = tk.StringVar(value="Остановлен")
        ttk.Label(status_grid, text="Статус:", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status_grid, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="w", padx=(5, 20))

        self.ip_count_var = tk.StringVar(value="0")
        ttk.Label(status_grid, text="Обнаружено IP:", style="Title.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(status_grid, textvariable=self.ip_count_var, style="Status.TLabel").grid(row=0, column=3, sticky="w", padx=(5, 20))

        self.lockdown_var = tk.StringVar(value="Не активна")
        ttk.Label(status_grid, text="Глоб. блокировка:", style="Title.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Label(status_grid, textvariable=self.lockdown_var, style="Status.TLabel").grid(row=0, column=5, sticky="w", padx=(5, 0))

        settings_frame = ttk.Frame(main_container)
        settings_frame.pack(fill="x", pady=(0, 10))

        capture_card = ttk.LabelFrame(settings_frame, text="Настройки захвата трафика", padding="10")
        capture_card.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.iface_var = tk.StringVar(value="wlp61s0")
        self.bpf_var = tk.StringVar(value="ip")

        ttk.Label(capture_card, text="Сетевой интерфейс:").pack(anchor="w")
        ttk.Entry(capture_card, textvariable=self.iface_var).pack(fill="x", pady=(2, 10))

        ttk.Label(capture_card, text="BPF фильтр:").pack(anchor="w")
        ttk.Entry(capture_card, textvariable=self.bpf_var).pack(fill="x", pady=(2, 0))

        block_card = ttk.LabelFrame(settings_frame, text="Автоматическая блокировка", padding="10")
        block_card.pack(side="left", fill="x", expand=True, padx=(5, 5))

        self.auto_block_var = tk.BooleanVar(value=False)
        self.threshold_var = tk.IntVar(value=10)
        self.window_var = tk.IntVar(value=30)
        self.duration_var = tk.IntVar(value=60)

        ttk.Checkbutton(block_card, text="Включить авто-блокировку", 
                       variable=self.auto_block_var).pack(anchor="w", pady=(0, 10))

        grid_frame = ttk.Frame(block_card)
        grid_frame.pack(fill="x")

        ttk.Label(grid_frame, text="Порог пакетов:").grid(row=0, column=0, sticky="w")
        tk.Spinbox(grid_frame, from_=1, to=100000, textvariable=self.threshold_var, 
                  width=8).grid(row=1, column=0, sticky="we", pady=(2, 5))

        ttk.Label(grid_frame, text="Окно (сек):").grid(row=0, column=1, sticky="w", padx=(10, 0))
        tk.Spinbox(grid_frame, from_=1, to=3600, textvariable=self.window_var, 
                  width=8).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(2, 5))

        ttk.Label(grid_frame, text="Длительность (сек):").grid(row=0, column=2, sticky="w", padx=(10, 0))
        tk.Spinbox(grid_frame, from_=0, to=86400, textvariable=self.duration_var, 
                  width=8).grid(row=1, column=2, sticky="we", padx=(10, 0), pady=(2, 5))

        control_card = ttk.LabelFrame(settings_frame, text="Управление", padding="10")
        control_card.pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.start_button = ttk.Button(control_card, text="Запуск мониторинга", 
                                      command=self.start_detector, width=20)
        self.start_button.pack(pady=5)

        self.stop_button = ttk.Button(control_card, text="Остановить мониторинг", 
                                     command=self.stop_detector, state="disabled", width=20)
        self.stop_button.pack(pady=5)

        ttk.Button(control_card, text="Очистить логи", 
                  command=self.clear_logs, width=20).pack(pady=5)

        data_frame = ttk.Frame(main_container)
        data_frame.pack(fill="both", expand=True)

        ip_frame = ttk.LabelFrame(data_frame, text="Обнаруженные IP-адреса")
        ip_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        ip_toolbar = ttk.Frame(ip_frame)
        ip_toolbar.pack(fill="x", pady=(0, 5))

        ttk.Button(ip_toolbar, text="Заблокировать", 
                  command=self.block_selected_ip).pack(side="left", padx=(0, 5))
        ttk.Button(ip_toolbar, text="В белый список", 
                  command=self.add_to_whitelist).pack(side="left", padx=(0, 5))
        ttk.Button(ip_toolbar, text="Удалить из белого списка", 
                  command=self.remove_from_whitelist).pack(side="left")
        ttk.Button(ip_toolbar, text="Разблокировать", 
                  command=self.unblock_selected_ip).pack(side="left", padx=(5, 0))

        listbox_frame = ttk.Frame(ip_frame)
        listbox_frame.pack(fill="both", expand=True)

        self.ip_listbox = tk.Listbox(listbox_frame, exportselection=False)
        ip_scroll = ttk.Scrollbar(listbox_frame, orient="vertical", command=self.ip_listbox.yview)
        self.ip_listbox.config(yscrollcommand=ip_scroll.set)
        self.ip_listbox.pack(side="left", fill="both", expand=True)
        ip_scroll.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(data_frame, text="Журнал событий безопасности")
        log_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", pady=(0, 5))

        ttk.Button(log_toolbar, text="Экспорт логов", 
                  command=self.export_logs).pack(side="left")

        log_text_frame = ttk.Frame(log_frame)
        log_text_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_text_frame, wrap="word", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_text_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def append_log(self, text: str):
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def add_ip_if_needed(self, ip: str):
        if ip in self.seen_ips:
            return
        self.seen_ips.add(ip)
        self._update_ip_display(ip)
        self.ip_count_var.set(str(len(self.seen_ips)))

    def _update_ip_display(self, ip: str):
        label = ip
        if is_whitelisted(ip):
            label += "  ✓"
        elif is_blocked(ip):
            label += "  ✗"
        
        items = self.ip_listbox.get(0, "end")
        for idx, text in enumerate(items):
            base_ip = text.split()[0]
            if base_ip == ip:
                self.ip_listbox.delete(idx)
                self.ip_listbox.insert(idx, label)
                return
        
        self.ip_listbox.insert("end", label)

    def _update_ip_status(self):
        if self.worker_thread and self.worker_thread.is_alive():
            items = self.ip_listbox.get(0, "end")
            for idx, text in enumerate(items):
                base_ip = text.split()[0]
                self._update_ip_display(base_ip)
        
        self.root.after(1000, self._update_ip_status)

    def _poll_log_queue(self):
        while True:
            try:
                line = log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(line + "\n")
            try:
                obj = json.loads(line)
            except Exception:
                continue
            src = obj.get("src")
            if src:
                self.add_ip_if_needed(src)
                
            if obj.get("reason") == "ddos_lockdown":
                self.lockdown_var.set("Активна")
            elif obj.get("reason") == "lockdown_drop":
                self.lockdown_var.set("Активна")
                
        self.root.after(200, self._poll_log_queue)

    def get_selected_ip(self) -> str | None:
        sel = self.ip_listbox.curselection()
        if not sel:
            return None
        text = self.ip_listbox.get(sel[0])
        return text.split()[0]

    def start_detector(self):
        global detector_settings, jsonl_path

        if self.worker_thread and self.worker_thread.is_alive():
            return

        iface = self.iface_var.get().strip() or "any"
        bpf = self.bpf_var.get().strip() or "ip"

        reset_detection_state()
        detector_settings = {
            "auto_block": bool(self.auto_block_var.get()),
            "block_threshold": max(1, int(self.threshold_var.get() or 1)),
            "block_window": max(1, int(self.window_var.get() or 1)),
            "block_duration": max(0, int(self.duration_var.get() or 0)),
        }
        jsonl_path = str(self.jsonl_path)

        self.seen_ips.clear()
        self.ip_listbox.delete(0, "end")
        self.ip_count_var.set("0")
        self.lockdown_var.set("Не активна")

        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(
            target=run_sniffer, args=(self.stop_event, iface, bpf), daemon=True
        )
        self.worker_thread.start()

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.status_var.set("Активен")

        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Мониторинг запущен\n")
        self.append_log(f" - Интерфейс: {iface}\n")
        self.append_log(f" - Фильтр: {bpf}\n")
        self.append_log(f" - Авто-блокировка: {'Вкл' if detector_settings['auto_block'] else 'Выкл'}\n\n")

    def stop_detector(self):
        if not self.worker_thread:
            return
        if self.stop_event:
            self.stop_event.set()
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Остановка мониторинга...\n")
        self.worker_thread.join(timeout=2.0)
        self.worker_thread = None
        self.stop_event = None
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.status_var.set("Остановлен")
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Мониторинг остановлен\n\n")

    def block_selected_ip(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("Информация", "Выберите IP в списке.")
            return
        dur = max(0, int(self.duration_var.get() or 0))
        if block_ip(ip, dur):
            self._update_ip_display(ip)
            self.append_log(
                f"[{datetime.now().strftime('%H:%M:%S')}] IP {ip} заблокирован на {dur} секунд (iptables)\n"
            )
        else:
            self.append_log(
                f"[{datetime.now().strftime('%H:%M:%S')}] Не удалось заблокировать {ip} (в белом списке)\n"
            )

    def unblock_selected_ip(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("Информация", "Выберите IP в списке.")
            return
        
        if unblock_ip(ip):
            self._update_ip_display(ip)
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] IP {ip} разблокирован (iptables)\n")
        else:
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] IP {ip} не был заблокирован\n")

    def add_to_whitelist(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("Информация", "Выберите IP в списке.")
            return
        ok = add_whitelist(ip)
        if ok:
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] IP {ip} добавлен в белый список\n")
        self._update_ip_display(ip)

    def remove_from_whitelist(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("Информация", "Выберите IP в списке.")
            return
        ok = remove_whitelist(ip)
        if ok:
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] IP {ip} удалён из белого списка\n")
        self._update_ip_display(ip)

    def clear_logs(self):
        self.log_text.delete(1.0, "end")
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Логи очищены\n")

    def export_logs(self):
        try:
            export_file = self.logs_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            with open(export_file, 'w', encoding='utf-8') as f:
                f.write(self.log_text.get(1.0, "end"))
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Логи экспортированы в {export_file}\n")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось экспортировать логи: {str(e)}")

def main():
    root = tk.Tk()
    app = DetectorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
