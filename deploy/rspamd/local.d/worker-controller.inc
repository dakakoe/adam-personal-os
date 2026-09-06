# Controller worker — the /learnspam, /learnham, /stat endpoints (Bayes
# training). Published 127.0.0.1:11334 on the host, same trust model as the
# normal worker: no password, but ONLY for clients matching secure_ip (merge_api
# arrives from the docker gateway, an RFC1918 address). If a future Rspamd
# version rejects passwordless learns despite secure_ip, switch to
# enable_password with an `rspamadm pw` bcrypt hash committed here.
bind_socket = "*:11334";
secure_ip = "127.0.0.1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16";
