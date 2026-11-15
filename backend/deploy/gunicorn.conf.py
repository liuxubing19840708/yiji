"""gunicorn 生产进程配置：systemd 单元 saofei.service 通过 -c 本文件启动。"""
bind = '127.0.0.1:8001'
workers = 3
threads = 2
timeout = 120          # 视频上传等慢请求
graceful_timeout = 30
accesslog = '/var/log/saofei/gunicorn-access.log'
errorlog = '/var/log/saofei/gunicorn-error.log'
capture_output = True
proc_name = 'saofei'
max_requests = 2000    # 防内存缓慢泄漏
max_requests_jitter = 200
