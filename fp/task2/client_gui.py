import socket
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from tkinter import filedialog
import os
import base64

DOWNLOAD_DIR = "downloads"


def ensure_download_dir() -> str:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return DOWNLOAD_DIR


def save_downloaded_file(filename: str, data: bytes) -> str:
    directory = ensure_download_dir()
    path = os.path.join(directory, filename)
    if os.path.exists(path):
        path = os.path.join(directory, f"copy_{filename}")
    with open(path, "wb") as f:
        f.write(data)
    return path


class ChatGUIClient:
    def __init__(self, master, host="127.0.0.1", port=8888):
        self.master = master
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self.running = False

        master.title("Asyncio Chat GUI")

        self.text_area = ScrolledText(master, state="disabled", height=20, width=70)
        self.text_area.pack(padx=5, pady=5, fill=tk.BOTH, expand=True)

        self.entry = tk.Entry(master, width=70)
        self.entry.pack(padx=5, pady=5, fill=tk.X)
        self.entry.bind("<Return>", self.send_message)

        self.btn_frame = tk.Frame(master)
        self.btn_frame.pack(padx=5, pady=5, fill=tk.X)

        self.connect_button = tk.Button(self.btn_frame, text="Подключиться", command=self.connect)
        self.connect_button.pack(side=tk.LEFT, padx=5)

        self.file_button = tk.Button(self.btn_frame, text="Отправить файл", command=self.send_file)
        self.file_button.pack(side=tk.LEFT, padx=5)

        self.quit_button = tk.Button(self.btn_frame, text="Выход", command=self.quit)
        self.quit_button.pack(side=tk.RIGHT, padx=5)

        master.protocol("WM_DELETE_WINDOW", self.quit)

    def append_text(self, text: str):
        self.text_area.configure(state="normal")
        self.text_area.insert(tk.END, text + "\n")
        self.text_area.see(tk.END)
        self.text_area.configure(state="disabled")

    def connect(self):
        if self.sock is not None:
            return
        try:
            self.sock = socket.create_connection((self.host, self.port))
            self.running = True
            self.append_text(f"Подключено к {self.host}:{self.port}")
            threading.Thread(target=self.reader_loop, daemon=True).start()
        except OSError as e:
            self.append_text(f"Ошибка подключения: {e}")

    def handle_filedata_line(self, line: str):
        """
        FILEDATA <filename> <base64> -> сохраняем файл в downloads/
        """
        parts = line.split(" ", 2)
        if len(parts) < 3:
            self.master.after(0, self.append_text, "[ОШИБКА] Неверный формат FILEDATA")
            return
        _, filename, b64 = parts
        try:
            data = base64.b64decode(b64.encode("utf-8"), validate=True)
        except Exception as e:
            self.master.after(0, self.append_text, f"[ОШИБКА] Не удалось декодировать FILEDATA: {e}")
            return
        path = save_downloaded_file(filename, data)
        self.master.after(0, self.append_text, f"[ФАЙЛ] Получен файл '{filename}', сохранён как '{path}'")

    def reader_loop(self):
        """
        Читает данные с сервера.
        - FILEDATA ... -> сохраняем файл
        - [ФАЙЛ] ... Путь на сервере: X -> автоматически шлём /d X
        - остальное выводим в чат
        """
        try:
            while self.running and self.sock:
                try:
                    data = self.sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    if line.startswith("FILEDATA "):
                        self.handle_filedata_line(line)
                        continue

                    # показываем текст в GUI
                    self.master.after(0, self.append_text, line)

                    # авто-запрос файла, если прилетело уведомление [ФАЙЛ]
                    if line.startswith("[ФАЙЛ]"):
                        marker = "Путь на сервере:"
                        idx = line.rfind(marker)
                        if idx != -1 and self.sock:
                            rel_path = line[idx + len(marker):].strip()
                            cmd = f"/d {rel_path}\n"
                            try:
                                self.sock.sendall(cmd.encode("utf-8"))
                                self.master.after(
                                    0,
                                    self.append_text,
                                    f"[КЛИЕНТ] Запрос файла: {rel_path}",
                                )
                            except OSError as e:
                                self.master.after(
                                    0,
                                    self.append_text,
                                    f"[ОШИБКА] Не удалось запросить файл: {e}",
                                )
        finally:
            self.master.after(0, self.append_text, "Соединение закрыто сервером.")
            self.running = False
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def send_message(self, event=None):
        if not self.sock:
            self.append_text("[!] Не подключено к серверу.")
            return
        msg = self.entry.get().strip()
        if not msg:
            return
        try:
            self.sock.sendall((msg + "\n").encode("utf-8"))
            if msg == "/quit":
                self.running = False
        except OSError as e:
            self.append_text(f"Ошибка отправки: {e}")
        finally:
            self.entry.delete(0, tk.END)

    def send_file(self):
        if not self.sock:
            self.append_text("[!] Не подключено к серверу.")
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        try:
            filename = os.path.basename(path)
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("utf-8")
            cmd = f"/file {filename} {b64}\n"
            self.sock.sendall(cmd.encode("utf-8"))
            self.append_text(f"[ЛОКАЛЬНО] Отправлен файл: {filename} ({len(data)} байт)")
        except OSError as e:
            self.append_text(f"Ошибка чтения файла: {e}")
        except Exception as e:
            self.append_text(f"Ошибка кодирования файла: {e}")

    def quit(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ChatGUIClient(root)
    root.mainloop()
