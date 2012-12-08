"""
Provides MonitorWorker, which currently only helps with logging.
"""
from workers import Worker

class MonitorWorker(Worker):

	def work(self):
		if not self.statusQueue:
			return
		while 1:
			status = self.statusQueue.get()
			print("%s : %s said %s" % (status.timestamp, status.who, status.status))
			self.statusQueue.task_done()



