#!/usr/bin/env python3
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if len(sys.argv) > 1 and sys.argv[1] == 'test':
    os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'
else:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
from django.core.management import execute_from_command_line
execute_from_command_line(sys.argv)
