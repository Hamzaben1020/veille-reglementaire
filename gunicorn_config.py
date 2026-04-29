# Configuration Gunicorn pour veille réglementaire SWAM
# Timeout augmenté pour permettre le scraping de tous les sites

bind = "0.0.0.0:10000"
workers = 1
timeout = 300  # 5 minutes au lieu de 30 secondes
worker_class = "sync"
accesslog = "-"
errorlog = "-"
loglevel = "info"
