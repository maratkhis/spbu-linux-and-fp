#!/bin/bash
sudo systemctl enable backup-daemon.service
sudo systemctl start backup-daemon.service
echo "автозагрузка включена"
