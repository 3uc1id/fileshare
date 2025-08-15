# fileshare.py is a Python program that shares a file via your LAN. 
# Copyright (C) 2025  Chris Calderon
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# You can contact me via email at 3ucli1.dev AT gmail.com
from socket import (
    socket, SOL_SOCKET, SO_REUSEADDR,
    AF_INET, SOCK_DGRAM, IPPROTO_UDP,
    IPPROTO_IP, IP_MULTICAST_TTL,
    INADDR_ANY, inet_aton, SHUT_RDWR,
    IP_ADD_MEMBERSHIP
)
import argparse
import os
import io
import threading
import struct
import select
import time

DEFAULT_CHUNKSIZE = 1 << 20  # 1 MiB
DEFAULT_PORT = 12345
LISTEN_TIMEOUT = 1.0 # seconds
HANDSHAKE_TCP_PORT = 23456
MULTICAST_GROUP = '224.0.0.1'
MARCO = b'MARCO'
POLO = b'POLO'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True, dest='cmd')
    parser_send = subparsers.add_parser('send', help='Send a file over the network.')
    parser_send.add_argument(
        'file',
        help='The path to the file you want to send.',
        type=argparse.FileType('rb', 0)
    )
    parser_recv = subparsers.add_parser('recv', help='Recieve a file sent over the network.')
    return parser.parse_args()


def server_handshake(stop: threading.Event) -> None:
    listener_udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
    listener_udp.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    listener_udp.bind(('0.0.0.0', DEFAULT_PORT))
    group_info = struct.pack("4sL", inet_aton(MULTICAST_GROUP), INADDR_ANY)
    listener_udp.setsockopt(IPPROTO_IP, IP_ADD_MEMBERSHIP, group_info)
    listener_udp.setblocking(False)

    while not stop.is_set():
        ready, *_ = select.select([listener_udp], [], [], LISTEN_TIMEOUT)
        if not ready:
            continue
            
        msg, addr = listener_udp.recvfrom(5)
        if msg != MARCO:
            continue

        print(f'Recieved MARCO from {addr[0]}')
        print('Responding with POLO')
        try:
            tcp_sock = socket()
            tcp_sock.connect((addr[0], HANDSHAKE_TCP_PORT))
            tcp_sock.sendall(POLO)
            tcp_sock.close()
        except Exception as exc:
            print(f'Error occurred during POLO response: {exc}')


def server_fileshare(stop: threading.Event, fd: io.FileIO) -> None:
    listener = socket()
    listener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    listener.bind(('0.0.0.0', DEFAULT_PORT))
    listener.listen()

    filename = os.path.basename(fd.name).encode()
    filename_length = len(filename).to_bytes(length=8)
    data_length = os.stat(fd.fileno()).st_size
    data_buf = bytearray(bytes(DEFAULT_CHUNKSIZE))
    buf_view = memoryview(data_buf)

    while not stop.is_set():
        ready, *_ = select.select([listener], [], [], LISTEN_TIMEOUT)
        if not ready:
            continue
        
        client, addr = listener.accept()
        print(f'Got a connection from {addr[0]}')
        print('Sending file...')
        client.send(filename_length)
        client.send(filename)
        client.send(data_length.to_bytes(length=8))
        data_sent = 0
        while data_sent < data_length:
            data_read = fd.readinto(data_buf)
            client.sendall(buf_view[:data_read])
            data_sent += data_read
        client.shutdown(SHUT_RDWR)
        client.close()
        fd.seek(0)
        print('Done.\n')

    listener.close()

    return

def send(args: argparse.Namespace) -> None:
    stop = threading.Event()
    handshake_thread = threading.Thread(target=server_handshake, args=(stop,))
    fileshare_thread = threading.Thread(target=server_fileshare, args=(stop, args.file))

    handshake_thread.start()
    fileshare_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        print('Shutting down handshake thread...')
        handshake_thread.join()
        print('Done.')
        print('Shutting down fileshare thread...')
        fileshare_thread.join()
        print('Done.')

def recv_all(conn: socket, length: int) -> bytearray:
    buf = bytearray()
    buf_len = 0
    while buf_len != length:
        chunk = conn.recv(length - buf_len)
        buf.extend(chunk)
        buf_len += len(chunk)
    return buf


def recv(args: argparse.Namespace) -> None:
    # Handshake section.
    # The file reciever initiates the handshakes and waits
    # for a response from the server in order to obtain the
    # server's IP address.
    print('Initiating handshake.')
    handshake_tcp = socket()
    handshake_tcp.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    handshake_tcp.bind(('0.0.0.0', HANDSHAKE_TCP_PORT))
    handshake_tcp.listen()

    handshake_udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
    handshake_udp.setsockopt(IPPROTO_IP, IP_MULTICAST_TTL, 1)
    handshake_udp.sendto(MARCO, (MULTICAST_GROUP, DEFAULT_PORT))


    msg = None
    server_ip = None
    while msg != POLO:
        conn, info = handshake_tcp.accept()
        msg = conn.recv(len(POLO))
        server_ip = info[0]
        conn.close()
    handshake_udp.close()
    handshake_tcp.close()
    print('Handshake complete.')
    # End handshake section

    # File transfer section
    print('Intitiating file transfer.')
    conn = socket()
    conn.connect((server_ip, DEFAULT_PORT))
    filename_length = int.from_bytes(recv_all(conn, 8))
    filename = recv_all(conn, filename_length)
    data_length = int.from_bytes(recv_all(conn, 8))
    data_buf = bytearray(bytes(DEFAULT_CHUNKSIZE))
    buf_view = memoryview(data_buf)
    total_data_recvd = 0
    with open(bytes(filename), 'wb', buffering=0) as f:
        percent_recvd = 0.0
        while total_data_recvd < data_length:
            print('.'*int(percent_recvd), end='\r')    
            data_recvd = conn.recv_into(data_buf)
            data_written = 0
            while data_written < data_recvd:
                data_written += f.write(buf_view[data_written:data_recvd])
            total_data_recvd += data_recvd
            percent_recvd = total_data_recvd / data_length
    conn.close()
    print('File transfer complete!')
    return


def main():
    args = parse_args()
    {'send': send,
     'recv': recv}[args.cmd](args)
    

if __name__ == '__main__':
    main()
