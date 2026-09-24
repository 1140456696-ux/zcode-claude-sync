#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一入口：claude_code 转 zcode 的一次性脚本（需配合交互选择目标）。"""
import os
import sys

# 让模块可被直接 ./run 启动
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zcsync.cli import main

if __name__ == "__main__":
    main()