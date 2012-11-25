"""
A basic non-caching proxy.

Features:
* multi-process architecture

Architecture:
* one listener listens on a port and places requests on a request queue
* one or more connect workers make the connections
* several workers forward the packets. One worker can serve multiple connections.
"""
from Queue import Empty

from multiprocessing.reduction import reduce_handle, rebuild_handle
import select

LISTEN_ADDRESS = '127.0.0.1'
LISTEN_PORT = 8888

import sys
import socket
import signal
import re

from multiprocessing import Process, JoinableQueue

running = True

def signalHandler(signum, frame):
	global running
	print("Received %d signal" % signum)
	if signum == signal.SIGTERM:
		running = False
		print("Quiting")


class Worker(object):
	"""
	Base class for workers.
	"""

	running = False

	def __init__(self, name):
		self.name = name

	def __str__(self):
		return self.name

	def work(self):
		raise NotImplementedError()


class ConnectWorker(Worker):
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

	def __init__(self, name, connectRequestsQueue, forwardingQueue):
		Worker.__init__(self, "Connect worker " + name)
		self.connectRequestsQueue = connectRequestsQueue
		self.forwardingQueue = forwardingQueue

	def connectTo(self, host, port):
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
					match = re.search('CONNECT ([^ ]*):([0-9]*) HTTP/1.1', all)
					if match:
						host = match.group(1)
						port = long(match.group(2))
						print("Connect requested to: %s:%s" % (host, port))
						serverSocket = self.connectTo(host, port)
						clientSocket.sendall("HTTP/1.1 200 Connection established\r\nProxy-Agent: Proxy.PY/0.1\r\n\r\n")
						self.forwardingQueue.put( (reduce_handle(clientSocket.fileno()),
												   reduce_handle(serverSocket.fileno())) )
						break
				else:
					clientSocket.close()
					break

		self.forwardingQueue.join()
		print("%s quitting..." % self)


class ForwardingWorker(Worker):
	"""
	Forwards packets between multiple socket pairs (client & server).
	"""

	sockets = [] #holds client, server sockets for select()

	def __init__(self, name, forwardingQueue):
		Worker.__init__(self, "Forward worker "+ name)
		self.forwardingQueue = forwardingQueue
		self.sockets = []
		self.socket2socket = {}

	def __addPair(self, clientSocket, serverSocket):
		print("Adding client & server pair %s, %s" % (clientSocket, serverSocket))
		self.sockets.extend([clientSocket, serverSocket])
		self.socket2socket[serverSocket] = clientSocket
		self.socket2socket[clientSocket] = serverSocket

	def __removePair(self, oneend):
		print("Removing socket %s" % oneend)
		otherend = self.socket2socket[oneend]
		self.sockets.remove(oneend)
		self.sockets.remove(otherend)
		self.socket2socket.pop(oneend)
		self.socket2socket.pop(otherend)

	def __getNewConnection(self, block=False):
		if block:
			clientHandle, serverHandle = self.forwardingQueue.get()
		else:
			try:
				clientHandle, serverHandle = self.forwardingQueue.get_nowait()
			except Empty:
				return
		clientSocket = socket.fromfd(rebuild_handle(clientHandle), socket.AF_INET, socket.SOCK_STREAM)
		serverSocket = socket.fromfd(rebuild_handle(serverHandle), socket.AF_INET, socket.SOCK_STREAM)
		self.forwardingQueue.task_done()

		self.__addPair(clientSocket, serverSocket)


	def work(self):
		self.running = True
		print("%s started working..." % self)

		self.__getNewConnection(block=True)

		sockets = self.sockets
		while self.running:
			# TODO: if we wait here we may never get a new connection
			readables, writables, exceptions = select.select(sockets, [], sockets)

			for exception in exceptions:
				sys.stderr.write(str(exception) + "\n")

			for readable in readables:
				buf = readable.recv(1024)
				other_end = self.socket2socket[readable]
				if buf:
					other_end.sendall(buf)
				else:
					self.__removePair(readable)
					readable.close()
					other_end.close()

			self.__getNewConnection()
		print("%s quitting...")


def main():
	"""
	Main entry point.
	"""
	signal.signal(signal.SIGTERM, signalHandler)

	connectRequestsQueue = JoinableQueue(20)
	forwardingQueue = JoinableQueue(20)

	processes = []
	print("Starting workers...")
	workers = [ ConnectWorker("first", connectRequestsQueue, forwardingQueue),
				ForwardingWorker("first", forwardingQueue)
			  ]
	for worker in workers:
		p = Process(target=worker.work, args=())
		processes.append(p)
		p.start()

	listeningSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
	listeningSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

	listeningSocket.bind((LISTEN_ADDRESS, LISTEN_PORT))
	listeningSocket.listen(1)

	try:
		while running:
			clientSocket, clientAddress = listeningSocket.accept()
			connectRequestsQueue.put(((reduce_handle(clientSocket.fileno())), clientAddress))
	except KeyboardInterrupt:
		print("Quitting... requested by user")
	finally:
		listeningSocket.close()

	#wait for all of the child processes
	# TODO: should stop the processes if not quitting not user requested
	print("Waiting for child processes...")
	for p in processes:
		p.join()
	print("Done!")

	return 0

if __name__ == "__main__":
	sys.exit(main())




