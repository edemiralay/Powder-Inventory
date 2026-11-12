import os

bind = '0.0.0.0:' + os.environ.get('PORT', '10000')
workers = 1
threads = 4
timeout = 240
accesslog = None
errorlog = '-'
wsgi_app = 'render_app:create_app()'
