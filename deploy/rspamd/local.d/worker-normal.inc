# The scanning worker: bind on all interfaces inside the container so the
# published 127.0.0.1:11333 host port reaches it. It serves the HTTP /checkv2
# scan endpoint that merge_api POSTs raw messages to (no password on scans).
bind_socket = "*:11333";
