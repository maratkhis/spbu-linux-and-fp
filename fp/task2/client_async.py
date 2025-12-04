import asyncio
import sys
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


def handle_filedata_line(line: str) -> None:
    """
    Обработка строки вида:
    FILEDATA <filename> <base64>
    """
    parts = line.split(" ", 2)
    if len(parts) < 3:
        print("[ОШИБКА] Неверный формат FILEDATA")
        return
    _, filename, b64 = parts
    try:
        data = base64.b64decode(b64.encode("utf-8"), validate=True)
    except Exception as e:
        print(f"[ОШИБКА] Не удалось декодировать FILEDATA: {e}")
        return
    path = save_downloaded_file(filename, data)
    print(f"[ФАЙЛ] Получен файл '{filename}', сохранён как '{path}'")


async def reader_task(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """
    Читает строки с сервера, показывает их,
    автоматически реагирует на:
      - FILEDATA ...  -> сохранить файл
      - [ФАЙЛ] ... Путь на сервере: X -> отправить /d X
    """
    try:
        while True:
            line = await reader.readline()
            if not line:
                print("\n[СЕРВЕР] Соединение закрыто.")
                break
            text = line.decode("utf-8").rstrip("\n")

            # 1) сервер прислал содержимое файла
            if text.startswith("FILEDATA "):
                handle_filedata_line(text)
                continue

            # 2) обычная строка — печатаем
            print(text)

            # 3) если это уведомление о загруженном файле — автозапрос
            if text.startswith("[ФАЙЛ]"):
                marker = "Путь на сервере:"
                idx = text.rfind(marker)
                if idx != -1:
                    rel_path = text[idx + len(marker):].strip()
                    cmd = f"/d {rel_path}\n"
                    try:
                        print(f"[КЛИЕНТ] Автоматически запрашиваю файл: {rel_path}")
                        writer.write(cmd.encode("utf-8"))
                        await writer.drain()
                    except Exception as e:
                        print(f"[ОШИБКА] Не удалось запросить файл: {e}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[ОШИБКА] В задаче чтения: {e!r}")


def build_file_command(path: str) -> str:
    """
    Преобразует локальный путь к файлу в команду /file <имя> <base64>.
    """
    path = path.strip().strip('"').strip("'")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode("utf-8")
    return f"/file {filename} {b64}"


async def main(host: str = "127.0.0.1", port: int = 8888):
    reader, writer = await asyncio.open_connection(host, port)
    print(f"Подключено к серверу {host}:{port}")
    print("Команды: /rooms, /w, /file <путь>, /d <путь_с_сервера>, /quit")

    # передаём writer в reader_task, чтобы он мог отправлять /d
    task = asyncio.create_task(reader_task(reader, writer))

    try:
        while True:
            # читаем ввод пользователя в отдельном потоке
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            text = line.rstrip("\n")

            # локовая команда /file <путь> -> перекодируем в base64-вариант
            if text.startswith("/file "):
                raw_path = text[len("/file "):]
                try:
                    text = build_file_command(raw_path)
                except FileNotFoundError:
                    print(f"[ОШИБКА] Файл не найден: {raw_path}")
                    continue
                except Exception as e:
                    print(f"[ОШИБКА] Не удалось прочитать файл: {e}")
                    continue

            writer.write((text + "\n").encode("utf-8"))
            await writer.drain()
            if text == "/quit":
                break
    except KeyboardInterrupt:
        print("\nКлиент остановлен.")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
