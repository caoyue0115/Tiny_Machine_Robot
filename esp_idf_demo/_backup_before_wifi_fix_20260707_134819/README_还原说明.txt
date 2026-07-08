备份时间: 20260707_134819
用途: A(关AMPDU RX稳链路) + B(HTTP keepalive/读超时) 改动前的原始文件

还原方法(在 esp_idf_demo 目录下执行):
  cp _backup_before_wifi_fix_20260707_134819/sdkconfig.orig                sdkconfig
  cp _backup_before_wifi_fix_20260707_134819/sdkconfig.defaults.orig       sdkconfig.defaults
  cp _backup_before_wifi_fix_20260707_134819/main/cloud_client.c.orig      main/cloud_client.c
  cp _backup_before_wifi_fix_20260707_134819/main/config.h.orig            main/config.h
