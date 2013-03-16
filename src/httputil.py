import re

__CONNECT_RE = re.compile('CONNECT ([^ ]*):([1-9]{1,5}) HTTP/1.1')
__HOST_RE = re.compile('Host: ([^ :\r\n]*)(:[0-9]{1,5})?')

HTTP_REQUEST_ENDING = "\r\n\r\n"

class HttpRequest(object):

	requestType = None
	requestedResource = None
	protocol = None
	options = None
	keys = None

	def __init__(self):
		self.keys = []
		self.options = dict()

	@staticmethod
	def buildFromBuffer(buffer):
		httpRequest = HttpRequest()

		ending = buffer.find(HTTP_REQUEST_ENDING)
		if ending <= 0:
			raise ValueError(buffer)

		lines = buffer[0:ending].splitlines()
		httpRequest.requestType, httpRequest.requestedResource, httpRequest.protocol = lines[0].split(" ")

		for line in lines[1:]:
			option, value = line.split(": ")
			httpRequest.options[option] = value
			httpRequest.keys.append(option)

		return httpRequest

	def makeRelative(self):
		rsc = self.requestedResource
		pos = rsc.find("://")
		if pos < 0:
			return
		uri = rsc[pos+3:]
		if "/" in uri:
			self.requestedResource = uri[uri.find("/"):]
		else:
			self.requestedResource = "/"

	def toBuffer(self):
		lines  = ["%s %s %s" % (self.requestType, self.requestedResource, self.protocol)]
		for key in self.keys:
			lines.append("%s: %s" % (key, self.options[key]))
		lines.append("\r\n")
		return "\r\n".join(lines)




