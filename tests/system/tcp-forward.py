#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forward a TCP port to another address (test helper).

Used by tests/system/run.sh when the host reaches the Internet through an HTTPS proxy
bound to its loopback interface, which the test container cannot reach: the forwarder
listens on the test network's gateway and relays to the proxy.

Usage: tcp-forward.py LISTEN_HOST LISTEN_PORT TARGET_HOST TARGET_PORT
"""

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def main(listen_host: str, listen_port: int, target_host: str, target_port: int) -> None:
    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(pipe(client_reader, target_writer), pipe(target_reader, client_writer))

    server = await asyncio.start_server(handle, listen_host, listen_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])))
