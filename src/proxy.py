"""
A basic non-caching proxy.

Features:
* multi-process architecture

Architecture:
* one listener listens on a port and places requests on a request queue
* one or more connect workers make the connections
* several workers forward the packets. One worker can serve multiple connections.

"""

LISTEN_ADDRESS = '127.0.0.1'
LISTEN_PORT = 8888

BUFFER_SIZE = 2836

import select
import sys
import socket
import signal
import re

from Queue import Empty
from multiprocessing.reduction import reduce_handle, rebuild_handle
from multiprocessing import Process, JoinableQueue



def getAddressFromBuffer(buf):
	"""
	Returns the host,port found in a buffer.
	"""
	match = HOST_RE.search(buf)
	if match:
		host = match.group(1)
		port = 80
		return host,port
	else:
		return None, None


class Socket(socket.SocketType):
	"""
	A socket with tx/rx statistics.
	"""
	tx = 0
	rx = 0

	def __init__(self, *args, **kwargs):
		socket.SocketType.__init__(self, *args, **kwargs)

	def recv(self, *args, **kwargs):
		buf = socket.SocketType.recv(self,*args, **kwargs)
		if buf:
			self.rx+=len(buf)

	def sendall(self, data, **kwargs):
		self.tx+=len(data)
		return socket.SocketType.sendall(self, data, **kwargs)


class Connection(object):

	clientSocket = None
	clientHandle = None
	clientAddress = None

	serverSocket = None
	serverHandle = None
	serverAddress = None

	def __init__(self, clientSocket, clientAddress, serverSocket, serverAddress):
		self.clientSocket = clientSocket
		self.clientAddress = clientAddress
		self.serverSocket = serverSocket
		self.serverAddress = serverAddress

	def __str__(self):
		return "%s -> %s " % (self.clientAddress, self.serverAddress)

	def reduce(self):
		""" Prepares for serialization """
		self.clientHandle = reduce_handle(self.clientSocket.fileno())
		self.serverHandle = reduce_handle(self.serverSocket.fileno())
		self.clientSocket = None
		self.serverSocket = None
		return self

	def rebuild(self):
		self.clientSocket = socket.fromfd(rebuild_handle(self.clientHandle), socket.AF_INET, socket.SOCK_STREAM)
		self.serverSocket = socket.fromfd(rebuild_handle(self.serverHandle), socket.AF_INET, socket.SOCK_STREAM)
		return self

	def close(self):
		#print("Closing sockets: %s & %s" % (self.clientSocket, self.serverSocket))
		try:
			self.clientSocket.shutdown(socket.SHUT_RDWR)
			self.clientSocket.close()
			self.serverSocket.shutdown(socket.SHUT_RDWR)
			self.serverSocket.close()
		except socket.error, why:
			sys.stderr.write("Connection %s closed: %s" % (self, str(why)))


class Worker(object):
	"""
	Base class for workers.
	"""
	_name = "Base worker"
	running = False

	def __init__(self, name):
		self.name = self._name + " " + name

	def __str__(self):
		return self.name

	def work(self):
		raise NotImplementedError()

HOST_RE = re.compile('Host: ([^ :\r\n]*):?([0-9]{0,5})')
CONNECT_RE = re.compile('CONNECT ([^ ]*):([0-9]*) HTTP/1.1')


class SwitchWorker(Worker):
	"""
	Handles establishing the connection between the target and the server

	From https://www.ietf.org/rfc/rfc2817.txt:

	5.2 Requesting a Tunnel with CONNECT

   	A CONNECT method requests that a proxy establish a tunnel connection
   	on its behalf. The Request-URI portion of the Request-Line is always
   	an 'authority' as defined by URI Generic Syntax [2], which is to say
   	the host name and port number destination of the requested connection
   	separated by a colon:

      CONNECT server.example.com:80 HTTP/1.1
      Host: server.example.com:80
	"""

	_name = "Switch worker"

	HTTP_CONNECTION_FAILED = "HTTP/1.1 404 Connection failed\r\n\r\n"

	def __init__(self, name, connectRequestsQueue, forwardingQueue, proxyingQueue):
		Worker.__init__(self, name)
		self.connectRequestsQueue = connectRequestsQueue
		self.forwardingQueue = forwardingQueue
		self.proxyingQueue = proxyingQueue

	@staticmethod
	def connectTo(host, port):
		serverSocket = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
		serverSocket.connect((host, port))
		return serverSocket

	def work(self):
		self.running = True
		print("%s started working..." % self)

		while self.running:
			clientHandle, clientAddress = self.connectRequestsQueue.get()
			print("Working with: ", clientAddress)
			clientSocket = socket.fromfd(rebuild_handle(clientHandle), socket.AF_INET, socket.SOCK_STREAM)
			all = ""
			while self.running:
				buf = clientSocket.recv(1024)
				print("Received %s" % buf)
				if buf:
					all+=buf
					match = re.search(CONNECT_RE, all)
					if match:
						host = match.group(1)
						port = long(match.group(2))
						print("Tunneling to: %s:%s" % (host, port))

						try:
							serverSocket = SwitchWorker.connectTo(host, port)
						except socket.error, why:
							sys.stderr.write(str(why) + "\n")
							clientSocket.sendall(self.HTTP_CONNECTION_FAILED)
							clientSocket.shutdown(socket.SHUT_RDWR)
							clientSocket.close()
							break

						clientSocket.sendall("HTTP/1.1 200 Connection established\r\nProxy-Agent: TunaPy/0.1\r\n\r\n")
						self.forwardingQueue.put( Connection(clientSocket, clientAddress, serverSocket, (host,port)).reduce())
						break

					match = HOST_RE.search(all)
					if match:
						# Not a CONNECT request
						host = match.group(1)
						port = 80
						print('Proxying to %s:%d' % (host, port))
						try:
							serverSocket = SwitchWorker.connectTo(host, port)
							# resend the client HTTP request to the server
							serverSocket.sendall(all)
						except socket.error, why:
							sys.stderr.write('An error occurred:\n%s\n' % str(why))
							clientSocket.sendall(self.HTTP_CONNECTION_FAILED)
							clientSocket.shutdown(socket.SHUT_RDWR)
							clientSocket.close()
							break

						print("Proxying queue size: %d" % self.proxyingQueue.qsize())
						self.proxyingQueue.put( Connection(clientSocket, clientAddress, serverSocket, (host,port)).reduce())
						break
				else:
					clientSocket.shutdown(socket.SHUT_RDWR)
					clientSocket.close()
					break

		self.forwardingQueue.join()
		print("%s quitting..." % self)


class ConnectionWorker(Worker):

	_name = "Connection worker"

	sockets = [] # holds client, server sockets for select()
	socket2socket = {} # maps sockets to other sockets
	socket2conn = {} # maps sockets to their connection

	SELECT_TIMEOUT = 0.05

	def __init__(self, name, newConnectionsQueue):
		Worker.__init__(self, name)
		self.__newConnectionsQueue = newConnectionsQueue
		self.sockets = []
		self.socket2socket = {}
		self.socket2conn = {}

	def _getNewConnection(self, block=False):
		if block:
			connection = self.__newConnectionsQueue.get()
		else:
			try:
				connection = self.__newConnectionsQueue.get_nowait()
			except Empty:
				return
		connection.rebuild()
		self.__newConnectionsQueue.task_done()
		self._addConnection(connection)

	def _addConnection(self, conn):
		print("%s adding connection %s" % (self, conn))
		clientSocket = conn.clientSocket
		serverSocket = conn.serverSocket
		self.sockets.extend([clientSocket, serverSocket])
		self.socket2socket[serverSocket] = clientSocket
		self.socket2socket[clientSocket] = serverSocket
		self.socket2conn[serverSocket] = conn
		self.socket2conn[clientSocket] = conn

	def _removeConnection(self, conn):
		self._removeSocket(conn.clientSocket)
		self._removeSocket(conn.serverSocket)

	def _closeConnection(self, conn, reason=""):
		print("%s closing connection %s because %s" % (self, conn, reason))
		self._removeConnection(conn)
		conn.close()

	def _removeSocket(self, oneEnd):
		self.sockets.remove(oneEnd)
		self.socket2socket.pop(oneEnd)
		self.socket2conn.pop(oneEnd)

	def _processBuffer(self, readable, buf):
		raise  NotImplementedError()

	def work(self):
		self.running = True
		print("%s started working..." % self)

		self._getNewConnection(block=True)

		sockets = self.sockets
		while self.running:
			readables, writables, exceptions = select.select(sockets, [], sockets, self.SELECT_TIMEOUT)

			for exception in exceptions:
				sys.stderr.write("Encountered: %s\n" + str(exception))

			for readable in readables:
				if not self.socket2socket.has_key(readable):
					continue
				endpoint = self.socket2socket[readable]
				conn = self.socket2conn[readable]
				if readable.fileno() != -1:
					try:
						buf = readable.recv(BUFFER_SIZE)
					except socket.error, why:
						sys.stderr.write("Encountered while recv: %s\n" % str(why))
						self._closeConnection(conn, str(why))
						continue
					if buf:
						self._processBuffer(readable, buf)
					else:
						self._closeConnection(conn, "buffer is empty")
				else:
					sys.stderr.write("-1 fd on %s, closing\n" % readable)
					self._closeConnection(conn, "socket fd is -1")

			self._getNewConnection()
		# TODO: need to close all connections on exit
		print("%s quitting...")


class TunnelWorker(ConnectionWorker):
	"""
	Forwards packets between multiple socket pairs (client & server).
	"""

	_name = "Tunnel worker"

	def __init__(self, name, newConnectionsQueue):
		ConnectionWorker.__init__(self, name, newConnectionsQueue)

	def _processBuffer(self, readable, buf):
		return self.socket2socket[readable].sendall(buf)


class ProxyWorker(ConnectionWorker):
	"""
	Handles proxying HTTP 1.1 requests for multiple connections.

	HTTP 1.1 rfc

    pg.13: In HTTP/1.0, most implementations used a new connection for each
    request/response exchange. In HTTP/1.1, a connection may be used for
    one or more request/response exchanges, although connections may be
    closed for a variety of reasons (see section 8.1).

	"""

	_name = "Proxy worker"

	def __init__(self, name, newConnectionsQueue):
		ConnectionWorker.__init__(self, name, newConnectionsQueue)

	def _processBuffer(self, readable, buf):
		conn = self.socket2conn[readable]
		if conn.clientSocket is readable:
			host, port = getAddressFromBuffer(buf)
			if host is not None:
				if (host,port) != conn.serverAddress:
					# observed behaviour was that a client may try to reuse a connection but with a different server
					# when this is the case the old server connection is replaced with the new one
					print("New connection requested to %s:%s from %s" % (host,port,conn))
					self._removeConnection(conn)
					conn.serverSocket.shutdown(socket.SHUT_RDWR)
					conn.serverSocket.close()
					try:
						conn.serverSocket = SwitchWorker.connectTo(host,port)
					except socket.error, why:
						sys.stderr.write("Failed to setup connection to %s:%s, reason: %s" % (host,port,why) )
					conn.serverAddress = host,port
					self._addConnection(conn)
			conn.serverSocket.sendall(buf)
		else:
			conn.clientSocket.sendall(buf)


running = True
def signalHandler(signum, frame):
	global running
	print("Received %d signal" % signum)
	if signum == signal.SIGTERM:
		running = False
		print("Quiting")


def main():
	signal.signal(signal.SIGTERM, signalHandler)

	connectRequestsQueue = JoinableQueue(20)
	forwardingQueue = JoinableQueue(20)
	proxyingQueue = JoinableQueue(20)

	processes = []
	print("Starting workers...")
	workers = [ SwitchWorker("Adam", connectRequestsQueue, forwardingQueue, proxyingQueue),
				TunnelWorker("Ted", forwardingQueue),
				ProxyWorker("Perseus", proxyingQueue),
				ProxyWorker("Penelope", proxyingQueue),
			]
	for worker in workers:
		p = Process(target=worker.work, args=())
		processes.append(p)
		p.start()

	listeningSocket = Socket(socket.AF_INET, socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
	listeningSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

	listeningSocket.bind((LISTEN_ADDRESS, LISTEN_PORT))
	listeningSocket.listen(10)

	try:
		while running:
			clientSocket, clientAddress = listeningSocket.accept()
			connectRequestsQueue.put(((reduce_handle(clientSocket.fileno())), clientAddress))
	except KeyboardInterrupt:
		print("Quitting... requested by user")
	finally:
		listeningSocket.close()

	#wait for all of the child processes
	# TODO: should stop the processes if quitting not user requested
	print("Waiting for child processes...")
	for p in processes:
		p.join()
	print("Done!")

	return 0

if __name__ == "__main__":
	sys.exit(main())




