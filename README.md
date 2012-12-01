tunapy
======

Tunapy is a very basic transparent, non-caching web-proxy that also supports tunneling. 

Note, it only works best with HTTPS and some HTTP sites do not currently work well at all.

Also, it does not alter the client requests in any way, as recommended per:
http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.45

Tested on Linux with python 2.7, depends on multiprocessing package support.

