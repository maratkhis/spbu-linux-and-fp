#!/usr/bin/env python3
import os
import sys
import time
import json
import logging
import shutil
from datetime import datetime
import daemon
from daemon import pidfile

class BackupDaemon:
    def __init__(self, config_file):
        self.config_file = config_file
        self.load_cfg()
        self.setup_logging()
        
    def load_cfg(self):
        with open(self.config_file, 'r') as f:
            self.config = json.load(f)        
        self.source_dir = self.config['source_dir']
        self.backup_dir = self.config['backup_dir']
        self.interval = self.config['backup_interval_minutes'] * 60
        self.log_file = self.config['log_file']
        self.pid_file = self.config['pid_file']

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file)
            ]
        )
        self.logger = logging.getLogger(__name__)

    def create_backup(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_path = os.path.join(self.backup_dir, backup_name)
        
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)
            
        if os.path.isdir(self.source_dir):
            shutil.copytree(self.source_dir, backup_path)
            self.logger.info(f"резервная копия создана: {backup_path}")
            return True
        else:
            self.logger.info("ошибка: исходная директория не существует")
            return False

    def run(self):
        self.logger.info("резервное копироание запущено")
        while True:
            try: 
                self.load_cfg()
                self.create_backup()
                self.logger.info("копирование завершено")
                time.sleep(self.interval)
            except KeyboardInterrupt:
                self.logger.info("оставновлен из-за ctrl+c")
                break
            except Exception as e:
                time.sleep(60)

def main():
    base_dir = os.path.join(os.path.expanduser("~"), "Desktop", "task1")
    config_file = os.path.join(base_dir, "config.json")
        
    backup_daemon = BackupDaemon(config_file)

    context = daemon.DaemonContext(
        pidfile=pidfile.TimeoutPIDLockFile(backup_daemon.pid_file),
        stdout=open(backup_daemon.log_file, 'a+'),
        stderr=open(backup_daemon.log_file, 'a+'),
        working_directory=base_dir)
    
    with context:
        backup_daemon.run()

if __name__ == "__main__":
    base_dir = os.path.join(os.path.expanduser("~"), "Desktop", "task1")
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "start":
            main()
        elif command == "stop":
            try:
                pid_file_path = os.path.join(base_dir, "daemon.pid")
                with open(pid_file_path, "r") as f:
                    pid = int(f.read().strip())
                os.kill(pid, 15)
                print("демон остановлен")
                
                if os.path.exists(pid_file_path):
                    os.remove(pid_file_path)
                    print("pid файл удален")
                
                    
            except Exception as e:
                print(f"ошибка при остановке демона: {e}")
        elif command == "status":
            try:
                pid_file_path = os.path.join(base_dir, "daemon.pid")
                with open(pid_file_path, "r") as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)
                print("демон работает")
            except Exception:
                print("демон не работает")
        else:
            print("использование: backup.py [start|stop|status]")
    else:
        print("использование: backup.py [start|stop|status]")
