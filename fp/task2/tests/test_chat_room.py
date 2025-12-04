
import asyncio
import pytest

from server import ChatRoom, Client

class FakeWriter:
    def __init__(self):
        self.buffer = []

    def write(self, data: bytes):
        self.buffer.append(data.decode("utf-8"))

    async def drain(self):
        await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_broadcast_to_all_clients():
    room = ChatRoom(name="test_room")
    task = asyncio.create_task(room.broadcaster())
    w1 = FakeWriter()
    w2 = FakeWriter()
    c1 = Client(name="user1", room=room, writer=w1)
    c2 = Client(name="user2", room=room, writer=w2)
    room.clients.add(c1)
    room.clients.add(c2)
    await room.queue.put(("user1", "hello everyone"))
    await asyncio.sleep(0.05)
    full1 = "".join(w1.buffer)
    full2 = "".join(w2.buffer)
    assert "hello everyone" in full1
    assert "hello everyone" in full2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
