#!/usr/bin/env python3
import os
import sys
import json
import subprocess

class BackupManager:
    def __init__(self):
        self.base_dir = os.path.join(os.path.expanduser("~"), "Desktop", "task1")
        self.config_file = os.path.join(self.base_dir, "config.json")
        self.script_path = os.path.join(self.base_dir, "backup.py")
    
    def start(self):
        try:
            subprocess.Popen([sys.executable, self.script_path, "start"])
            print("демон запущен")
        except Exception as e:
            print(f"ошибка при запуске демона: {e}")
    
    def stop(self):
        try:
            subprocess.run([sys.executable, self.script_path, "stop"])
        except Exception as e:
            print(f"ошибка при остановке демона: {e}")
    
    def status(self):
        try:
            subprocess.run([sys.executable, self.script_path, "status"])
        except Exception as e:
            print(f"ошибка при проверке статуса: {e}")
    
    def show_config(self):
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            print("текущая конфигурация:")
            for key, value in config.items():
                print(f"  {key}: {value}")
        except Exception as e:
            print(f"ошибка при чтении конфигурации: {e}")

if __name__ == "__main__":
    manager = BackupManager()
    
    if len(sys.argv) < 2:
        print("использование: manager.py [start|stop|status|config]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "start":
        manager.start()
    elif command == "stop":
        manager.stop()
    elif command == "status":
        manager.status()
    elif command == "config":
        manager.show_config()
    else:
        print("неизвестная команда")
