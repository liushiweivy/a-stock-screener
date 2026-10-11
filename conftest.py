# -*- coding: utf-8 -*-
"""pytest 引导：把项目根加入 sys.path。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
