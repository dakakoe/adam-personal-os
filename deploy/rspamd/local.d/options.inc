# merge_api reaches Rspamd from the docker host gateway (not 127.0.0.1 inside the
# container), so allow scan requests from the compose networks. These are private
# RFC1918 ranges; the host port is bound to 127.0.0.1 only, so nothing external
# can reach the worker regardless.
local_addrs = "127.0.0.1, 172.16.0.0/12, 192.168.0.0/16, 10.0.0.0/8";
