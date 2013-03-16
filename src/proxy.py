"""
A basic non-caching proxy.

Features:
* multi-process architecture

Architecture:
* one listener listens on a port and places requests on a request queue
* one or more connect workers make the connections
* several workers forward the packets. One worker can serve multiple connections.

"""
import os
import sys
import socket
import signal
import time

from optparse import OptionParser

from multiprocessing import Process, JoinableQueue, Array

from net import Socket, Endpoint, Address
from monitor import MonitorWorker
from workers import SwitchWorker, ForwardingWorker, DirectProxyWorker, Worker

LISTEN_ADDRESS = '127.0.0.1'
LISTEN_PORT = 8888

running = True

def signalHandler(signum, frame):
	global running
	print("Received %d signal from %s" % (signum, frame))
	if signum == signal.SIGTERM:
		running = False
		print("Quiting")


class ProxyOptions(OptionParser):
	def __init__(self):
		OptionParser.__init__(self)
		self.add_option("-p", "--port",  type=int,
						help="port to listen on, default %s" % LISTEN_PORT)
		self.add_option("-l", "--listen", metavar="ADDRESS",
						help="address to listen on, default %s" % LISTEN_ADDRESS)
		self.add_option("-f", "--forward", metavar="HOST:PORT",
						default=[], action="append",
						help="forward to the next proxy server")
		self.add_option("-P", "--proxycfg", metavar="FILE",
						type=str, default=None,
						help="file that contains the proxy configuration")


class ConfigUpdater(Worker):
	"""
	Updates the shared configuration from a file.
	"""

	def __init__(self, config, sharedConfig, statusQueue):
		Worker.__init__(self, 'Config updater', statusQueue)
		self.configFile = config
		self.sharedConfig = sharedConfig

	def work(self):
		Worker.work(self)
		oldRawConfig = ""
		while self.running:
			rawConfig = open(self.configFile).read()
			if rawConfig != oldRawConfig:
				self.say("Updating config to: %s" % rawConfig)
				self.sharedConfig.value = rawConfig
				oldRawConfig = rawConfig
			#TODO: replace this with an event
			time.sleep(10)


def main():
	options, remainingArgs = ProxyOptions().parse_args()
	listenAddress = options.listen or LISTEN_ADDRESS
	listenPort = options.port or LISTEN_PORT

	statusQueue = JoinableQueue(20)
	sharedConfig = Array('c', 100)

	processes = []

	proxyList = []
	if options.forward:
		print('Will forward to: %s' % options.forward)
		for proxy in options.forward:
			host, port = proxy.split(":")
			proxyList.append( Address(host, port))
	elif options.proxycfg:
		if not os.path.isfile(options.proxycfg):
			raise ValueError("No such file: %s" % options.proxycfg)
		cfgUpdater = ConfigUpdater(options.proxycfg, sharedConfig, statusQueue)
		p = Process(target=cfgUpdater.work, args=())
		processes.append(p)
		p.start()

	print('Forwarding to proxies: %s' % str(proxyList))

	signal.signal(signal.SIGTERM, signalHandler)

	connectRequestsQueue = JoinableQueue(20)
	forwardingQueue = JoinableQueue(20)
	proxyingQueue = JoinableQueue(20)

	print("Starting workers...")
	workers = [ SwitchWorker("Adam", connectRequestsQueue, forwardingQueue, proxyingQueue),
				ForwardingWorker("Fred", forwardingQueue),
				DirectProxyWorker("Perseus", proxyingQueue),
				DirectProxyWorker("Penelope1", proxyingQueue),
				DirectProxyWorker("Penelope2", proxyingQueue),
				DirectProxyWorker("Penelope3", proxyingQueue),
				MonitorWorker("Mo"),
			]
	for worker in workers:
		if isinstance(worker, SwitchWorker):
			worker.proxyList = proxyList
		worker.statusQueue = statusQueue
		worker.sharedConfig = sharedConfig
		p = Process(target=worker.work, args=())
		processes.append(p)
		p.start()

	listeningSocket = Socket(socket.AF_INET, socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
	listeningSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

	listeningSocket.bind((listenAddress, listenPort))
	listeningSocket.listen(10)
	print("Listening on %s:%d" % (listenAddress, listenPort))

	try:
		while running:
			clientSocket, clientAddress = listeningSocket.accept()
			client = Endpoint(clientSocket, clientAddress)
			connectRequestsQueue.put(client.reduce())
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




