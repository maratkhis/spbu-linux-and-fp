#!/bin/bash
sudo systemctl stop backup-daemon.service
sudo systemctl disable backup-daemon.service
echo "автозагрузка выключена"
