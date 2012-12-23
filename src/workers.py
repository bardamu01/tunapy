import os
import sys
import socket
import select
from Queue import Empty
from httputil import HttpRequest

from net import Endpoint, Connection
from status import Status

BUFFER_SIZE = 2836


class Worker(object):
	"""
	Base class for workers.
	"""
	_name = "Base worker"
	running = False
	statusQueue = None

	def __init__(self, name, statusQueue=None):
		self.name = "%s %s" % (self._name, name)
		self.statusQueue = statusQueue

	def __str__(self):
		return self.name + " (%d)" % os.getpid()

	def work(self):
		self.say("Started working")
		self.running = True

	def say(self, message):
		statement = "%s said: %s" % (self, message)
		if self.statusQueue:
			self.statusQueue.put(Status(self.name, message))
		else:
			print(statement)

	def quit(self):
		# TODO: need to close all connections on exit
		self.say("Quitting...")


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
	proxyList = []

	HTTP_CONNECTION_FAILED = "HTTP/1.1 404 Connection failed\r\n\r\n"

	def __init__(self, name, connectRequestsQueue, forwardingQueue, proxyingQueue, statusQueue=None):
		Worker.__init__(self, name, statusQueue)
		self.connectRequestsQueue = connectRequestsQueue
		self.forwardingQueue = forwardingQueue
		self.proxyingQueue = proxyingQueue


	def __getProxy(self):
		"""
		Tries to find a reachable proxy. Makes it first proxy if found.
		"""
		for proxy in self.proxyList:
			try:
				ret = Endpoint.connectTo(proxy[0], proxy[1])
				if proxy != self.proxyList[0]:
					self.say("Making %s first proxy" % str(proxy))
					self.proxyList.remove(proxy)
					self.proxyList = [proxy] + self.proxyList
				return ret
			except socket.error:
				self.say("Failed to connect to proxy %s" % str(proxy))
		return None

	def work(self):
		Worker.work(self)
		while self.running:
			client = self.connectRequestsQueue.get()
			client.rebuild()
			self.say("New client: %s " % client)
			while self.running:
				buf = client.socket.recv(BUFFER_SIZE)
				if buf:
					self.say("Received %s" % buf)
					if self.proxyList:
						proxy = self.__getProxy()
						self.say("Forwarding to next proxy: %s" % str(proxy))
						try:
							proxy.socket.sendall(buf)
						except socket.error, why:
							sys.stderr.write(why.message + "\n")
							client.shutdown()
							break
						self.forwardingQueue.put( Connection(client, proxy).reduce())
						break
					else:
						httpRequest = HttpRequest.buildFromBuffer(buf)
						if httpRequest.requestType == "CONNECT":
							host, port = httpRequest.requestedResource.split(":")
							self.say("Tunneling to: %s:%s" % (host, port))
							try:
								server = Endpoint.connectTo(host, long(port))
							except socket.error, why:
								sys.stderr.write(why.message + "\n")
								client.shutdown()
								break

							client.socket.sendall("HTTP/1.1 200 Connection established\r\nProxy-Agent: TunaPy/0.1\r\n\r\n")
							self.forwardingQueue.put( Connection(client, server).reduce())
							break
						else:
							httpRequest.makeRelative()
							host = httpRequest.options['Host']
							port = 80
							self.say('Proxying to %s:%d' % (host, port))
							try:
								server = Endpoint.connectTo(host, port)
								# resend the client HTTP request to the server
								self.say("Sending: %s" % httpRequest.toBuffer())
								server.socket.sendall(httpRequest.toBuffer())
							except socket.error, why:
								sys.stderr.write('An error occurred:\n%s\n' % why.message)
								client.shutdown()
								break

							self.say("Proxying queue size: %d" % self.proxyingQueue.qsize())
							self.proxyingQueue.put( Connection(client, server).reduce())
							break
				else:
					client.shutdown()
					break

		self.forwardingQueue.join()
		self.quit()


class ConnectionWorker(Worker):

	_name = "Connection worker"

	sockets = [] # holds client, server sockets for select()
	socket2socket = {} # maps sockets to other sockets
	socket2conn = {} # maps sockets to their connection

	SELECT_TIMEOUT = 0.05

	def __init__(self, name, newConnectionsQueue, statusQueue):
		Worker.__init__(self, name, statusQueue)
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
		self.say("Adding connection %s" % conn)
		clientSocket = conn.client.socket
		serverSocket = conn.server.socket
		self.sockets.extend([clientSocket, serverSocket])
		self.socket2socket[serverSocket] = clientSocket
		self.socket2socket[clientSocket] = serverSocket
		self.socket2conn[serverSocket] = conn
		self.socket2conn[clientSocket] = conn

	def _removeConnection(self, conn):
		self._removeSocket(conn.client.socket)
		self._removeSocket(conn.server.socket)

	def _closeConnection(self, conn, reason=""):
		self.say("Closing connection %s because %s" % (conn, reason))
		self._removeConnection(conn)
		conn.close()

	def _removeSocket(self, oneEnd):
		self.sockets.remove(oneEnd)
		self.socket2socket.pop(oneEnd)
		self.socket2conn.pop(oneEnd)

	def _processBuffer(self, readable, buf):
		raise  NotImplementedError()

	def work(self):
		Worker.work(self)

		self._getNewConnection(block=True)

		sockets = self.sockets
		while self.running:
			readables, writables, exceptions = select.select(sockets, [], sockets, self.SELECT_TIMEOUT)

			for exception in exceptions:
				sys.stderr.write("Encountered: %s\n" + str(exception))

			for readable in readables:
				if not self.socket2socket.has_key(readable):
					continue
				conn = self.socket2conn[readable]
				if readable.fileno() != -1:
					try:
						buf = readable.recv(BUFFER_SIZE)
					except socket.error, why:
						sys.stderr.write("Encountered while recv: %s\n" % why.message)
						self._closeConnection(conn, why.message)
						continue
					if buf:
						self._processBuffer(readable, buf)
					else:
						self._closeConnection(conn, "buffer is empty")
				else:
					sys.stderr.write("-1 fd on %s, closing\n" % readable)
					self._closeConnection(conn, "socket fd is -1")

			self._getNewConnection()
		self.quit()


class ForwardingWorker(ConnectionWorker):
	"""
	Forwards packets between multiple socket pairs (client & server).
	"""

	_name = "Tunnel worker"

	def __init__(self, name, newConnectionsQueue, statusQueue=None):
		ConnectionWorker.__init__(self, name, newConnectionsQueue, statusQueue=None)

	def _processBuffer(self, readable, buf):
		try:
			self.socket2socket[readable].sendall(buf)
		except socket.error, why:
			conn = self.socket2conn[readable]
			self._closeConnection(conn, "Could not send buffer: %s" % why.message)


class ProxyWorker(ConnectionWorker):
	"""
	Handles proxying HTTP 1.1 requests for multiple connections.

	HTTP 1.1 rfc, pg.13:
		In HTTP/1.0, most implementations used a new connection for each
    request/response exchange. In HTTP/1.1, a connection may be used for
    one or more request/response exchanges, although connections may be
    closed for a variety of reasons (see section 8.1).

	"""

	_name = "Proxy worker"

	def __init__(self, name, newConnectionsQueue, statusQueue=None):
		ConnectionWorker.__init__(self, name, newConnectionsQueue, statusQueue)

	def _processBuffer(self, readable, buf):
		conn = self.socket2conn[readable]
		if conn.client.socket is readable:
			httpRequest= HttpRequest.buildFromBuffer(buf)
			host = httpRequest.options['Host']
			port=80
			if host is not None and (host, port) != conn.server.address:
				# observed behaviour was that a client may try to reuse a connection but with a different server
				# when this is the case the old server connection is replaced with the new one
				self.say("New connection requested to %s:%s from %s" % (host,port,conn))
				self._removeConnection(conn)
				conn.server.shutdown()
				try:
					conn.server= Endpoint.connectTo(host,port)
				except socket.error, why:
					sys.stderr.write("Failed to setup connection to %s:%s, reason: %s" % (host,port,why) )
					return
				self._addConnection(conn)
			httpRequest.makeRelative()
			conn.server.socket.sendall(httpRequest.toBuffer())
		else:
			try:
				conn.client.socket.sendall(buf)
			except socket.error, why:
				self.say("Could not send buffer on %s because %s" % (conn, why))
				self._closeConnection(conn)

