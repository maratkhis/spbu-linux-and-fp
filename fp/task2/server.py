
import asyncio
import base64
import os
from dataclasses import dataclass, field
from typing import Dict, Set, Optional

UPLOAD_DIR = "uploaded_files"

os.makedirs(UPLOAD_DIR, exist_ok=True)

@dataclass(eq=False)
class Client:
    name: str
    room: "ChatRoom"
    writer: asyncio.StreamWriter

    async def send(self, message: str) -> None:
        try:
            self.writer.write((message + "\n").encode("utf-8"))
            await self.writer.drain()
        except ConnectionError:
            pass

@dataclass
class ChatRoom:
    name: str
    clients: Set[Client] = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    broadcaster_task: Optional[asyncio.Task] = None

    async def broadcaster(self) -> None:
        while True:
            sender, text = await self.queue.get()
            msg = f"[{self.name}] {sender}: {text}"
            dead_clients = []
            for client in list(self.clients):
                try:
                    await client.send(msg)
                except Exception:
                    dead_clients.append(client)
            for dc in dead_clients:
                self.clients.discard(dc)
            self.queue.task_done()

rooms: Dict[str, ChatRoom] = {}
rooms_lock = asyncio.Lock()

async def get_or_create_room(name: str) -> ChatRoom:
    async with rooms_lock:
        room = rooms.get(name)
        if room is None:
            room = ChatRoom(name=name)
            room.broadcaster_task = asyncio.create_task(room.broadcaster())
            rooms[name] = room
        return room

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    addr = writer.get_extra_info("peername")
    print(f"Новое подключение: {addr}")
    client: Optional[Client] = None
    try:
        await send_raw(writer, "Введите ваш ник: ")
        name = await read_line(reader)
        if not name:
            name = f"{addr[0]}:{addr[1]}"
        await send_raw(writer, "Введите имя комнаты (например, general): ")
        room_name = await read_line(reader)
        if not room_name:
            room_name = "general"
        room = await get_or_create_room(room_name)
        client = Client(name=name, room=room, writer=writer)
        room.clients.add(client)
        await room.queue.put(("SERVER", f"{name} вошёл в комнату {room_name}"))
        await client.send(
            f"Добро пожаловать в комнату '{room_name}', {name}!\n"
            "Команды:\n"
            "  /rooms               — список комнат\n"
            "  /w <ник> <текст>     — личное сообщение\n"
            "  /file <имя> <base64> — загрузка файла (используется клиентами автоматически)\n"
            "  /d <путь>            — скачать файл по пути с сервера\n"
            "  /quit                — выйти\n"
        )
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8").rstrip("\r\n")
            if not text:
                continue
            if text == "/quit":
                await client.send("Выход из чата...")
                break
            if text == "/rooms":
                await handle_rooms(client)
            elif text.startswith("/w "):
                await handle_private_message(client, text)
            elif text.startswith("/file "):
                await handle_file_upload(client, text)
            elif text.startswith("/d "):
                await handle_file_download(client, text)
            else:
                await room.queue.put((client.name, text))
    except Exception as e:
        print(f"Ошибка в обработке клиента {addr}: {e!r}")
    finally:
        if client is not None:
            client.room.clients.discard(client)
            try:
                await client.room.queue.put(("SERVER", f"{client.name} покинул комнату"))
            except RuntimeError:
                pass
        try:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass
        print(f"Клиент отключён: {addr}")

async def handle_rooms(client: Client) -> None:
    async with rooms_lock:
        if not rooms:
            await client.send("Нет активных комнат.")
            return

        lines = ["Список комнат:"]
        for name, room in rooms.items():
            usernames = ", ".join(c.name for c in room.clients) or "нет пользователей"
            lines.append(f"- {name} ({len(room.clients)} клиентов: {usernames})")
        await client.send("\n".join(lines))


async def handle_private_message(client: Client, text: str) -> None:
    parts = text.split(" ", 2)
    if len(parts) < 3:
        await client.send("Формат ЛС: /w <ник> <сообщение>")
        return
    _, target_name, msg = parts
    room = client.room
    target = next((c for c in room.clients if c.name == target_name), None)
    if not target:
        await client.send(f"Пользователь '{target_name}' не найден в комнате.")
        return
    await target.send(f"[ЛС от {client.name}]: {msg}")
    await client.send(f"[ЛС для {target.name}]: {msg}")

async def handle_file_upload(client: Client, text: str) -> None:
    parts = text.split(" ", 2)
    if len(parts) < 3:
        await client.send("Неверный формат команды /file")
        return
    _, filename, b64 = parts
    try:
        data = base64.b64decode(b64.encode("utf-8"), validate=True)
    except Exception:
        await client.send("Не удалось декодировать файл (base64).")
        return
    room_dir = os.path.join(UPLOAD_DIR, client.room.name)
    os.makedirs(room_dir, exist_ok=True)
    safe_name = filename.replace("/", "_").replace("\\", "_")
    path = os.path.join(room_dir, f"{client.name}_{safe_name}")
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError as e:
        await client.send(f"Ошибка сохранения файла: {e}")
        return
    size = len(data)
    rel_path = os.path.relpath(path, UPLOAD_DIR)
    msg = f"[ФАЙЛ] {client.name} загрузил файл '{filename}' ({size} байт). Путь на сервере: {rel_path}"
    await client.room.queue.put(("SERVER", msg))

async def handle_file_download(client: Client, text: str) -> None:
    parts = text.split(" ", 1)
    if len(parts) < 2:
        await client.send("Формат: /d <путь_к_файлу_на_сервере>")
        return

    rel_path = parts[1].strip()
    if not rel_path:
        await client.send("Формат: /d <путь_к_файлу_на_сервере>")
        return

    base_dir = os.path.abspath(UPLOAD_DIR)
    full_path = os.path.abspath(os.path.normpath(os.path.join(base_dir, rel_path)))

    if not full_path.startswith(base_dir + os.sep):
        await client.send("Недопустимый путь к файлу.")
        return

    if not os.path.exists(full_path):
        await client.send("Файл не найден на сервере.")
        return

    try:
        with open(full_path, "rb") as f:
            data = f.read()
    except OSError as e:
        await client.send(f"Ошибка чтения файла: {e}")
        return

    b64 = base64.b64encode(data).decode("utf-8")
    filename = os.path.basename(full_path)
    await client.send(f"FILEDATA {filename} {b64}")


async def send_raw(writer: asyncio.StreamWriter, text: str) -> None:
    writer.write(text.encode("utf-8"))
    await writer.drain()

async def read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    return line.decode("utf-8").strip()

async def main(host: str = "127.0.0.1", port: int = 8888):
    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"Сервер запущен на {addrs}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nСервер остановлен (Ctrl+C).")
