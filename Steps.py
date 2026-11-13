# Normal deploy + restart (prompts for IP and port):
deploy

# Skip restarting the app:
deploy -SkipServe

# Supply address on the command line to skip the prompts:
deploy -ListenIP 192.168.1.20 -Port 8000
